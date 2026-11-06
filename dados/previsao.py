"""Chuva e temperatura máxima previstas para as próximas 72 h pelo modelo Eta (INPE/CPTEC, 40 km).

Cada arquivo horário do Eta traz no parâmetro 13 ("prec") a chuva da hora que termina
no horário do arquivo, em metros. O .ctl diz "Total 6h Precip.", mas os valores são
horários: conferido em 31/10/2026, o máximo do campo cai de uma hora para a seguinte,
o que não aconteceria com um acumulado. O parâmetro 6 ("mxtp") é a temperatura máxima
a 2 m na hora, em kelvin. As mensagens ficam sempre no mesmo trecho do arquivo (nas
rodadas de out/2026, a temperatura máxima nos bytes 226.500 a 271.800 e a chuva de
543.600 a 590.578), então baixamos só uma janela que cobre as duas e, se não bastar, o
primeiro 1 MB.

O coletor (`coletar --fonte previsao_chuva`) grava a rodada mais recente completa:
HORAS_GUARDADAS horas, para que ainda haja 72 h à frente até a rodada seguinte sair.
São ~40 MB baixados a cada 12 h. A situação das regionais usa as horas a partir de agora.
"""

import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import vento
from .chuva import GradeNoMapa
from .coletores.http import ErroDeColeta, baixar
from .models import PrevisaoChuva, PrevisaoTemperatura

PARAMETRO = 13  # chuva da hora (m)
PARAMETRO_TEMPERATURA = 6  # temperatura máxima a 2 m na hora (K)
JANELA = (220_000, 600_000)  # bytes em volta das duas mensagens
HORAS = 72
HORAS_GUARDADAS = 108  # 72 h à frente mesmo 36 h depois do início da rodada
GUARDAR_RODADAS = 2
DOWNLOADS_SIMULTANEOS = 4


def campos_da_hora(conteudo):
    """GRIB1 (trecho) → (geometria, chuva em mm, temperatura máxima em °C); None no que faltar no trecho."""
    geometria, campos = vento.ler_campos(conteudo, (PARAMETRO, PARAMETRO_TEMPERATURA))
    chuva = campos[PARAMETRO] * 1000 if PARAMETRO in campos else None  # m → mm
    temperatura = campos[PARAMETRO_TEMPERATURA] - 273.15 if PARAMETRO_TEMPERATURA in campos else None
    return geometria, chuva, temperatura


def _baixar_hora(rodada, valido):
    url = vento.URL_HORA.format(rodada=rodada, valido=valido)
    trecho = baixar(url, cabecalhos={'Range': f'bytes={JANELA[0]}-{JANELA[1]}'}, tentativas=3, timeout=60)
    geometria, chuva, temperatura = campos_da_hora(trecho.content)
    if (chuva is None or temperatura is None) and trecho.status_code == 206:
        inicio = baixar(url, cabecalhos={'Range': 'bytes=0-1048575'}, tentativas=2, timeout=60)
        geometria, chuva, temperatura = campos_da_hora(inicio.content)
    if chuva is None or temperatura is None:
        raise ErroDeColeta(f'a chuva ou a temperatura prevista não está no arquivo de {valido:%d/%m %Hh} UTC')
    return geometria, chuva, temperatura


def rodada_completa_mais_recente(agora):
    """Rodada mais nova que já publicou a última hora que guardamos. Levanta ErroDeColeta."""
    for rodada in vento.rodadas_candidatas(agora.replace(minute=0, second=0, microsecond=0)):
        ultima = vento.URL_HORA.format(rodada=rodada, valido=rodada + timedelta(hours=HORAS_GUARDADAS))
        try:
            baixar(ultima, cabecalhos={'Range': 'bytes=0-0'}, tentativas=1, timeout=30)
            return rodada
        except ErroDeColeta:
            continue
    raise ErroDeColeta('nenhuma rodada recente do Eta está completa no FTP do CPTEC')


def coletar(agora=None):
    """Grava a chuva e a temperatura previstas da rodada completa mais recente, se ainda não estiverem no banco."""
    import numpy as np

    rodada = rodada_completa_mais_recente(agora or datetime.now(timezone.utc))
    modelos = (PrevisaoChuva, PrevisaoTemperatura)
    if all(modelo.objects.filter(rodada=rodada).exists() for modelo in modelos):
        return 0
    horas = [rodada + timedelta(hours=h) for h in range(1, HORAS_GUARDADAS + 1)]
    with ThreadPoolExecutor(DOWNLOADS_SIMULTANEOS) as grupo:
        resultados = list(grupo.map(lambda valido: _baixar_hora(rodada, valido), horas))
    geometria = resultados[0][0]
    for modelo, indice in zip(modelos, (1, 2)):
        grade = np.stack([np.round(r[indice], 1) for r in resultados]).astype('<f4')
        modelo.objects.update_or_create(rodada=rodada, defaults={
            'primeira_hora': horas[0], 'horas': len(horas), **geometria,
            'linhas': grade.shape[1], 'colunas': grade.shape[2], 'grade': zlib.compress(grade.tobytes(), 6),
        })
        velhas = list(modelo.objects.values_list('pk', flat=True)[GUARDAR_RODADAS:])
        modelo.objects.filter(pk__in=velhas).delete()
    return len(horas)


def serie_no_ponto(previsao, valores, latitude, longitude):
    """Valor de cada hora prevista no ponto (interpolação bilinear; células sem dado ficam de fora)."""
    import numpy as np

    y = (latitude - previsao.lat_sul) / previsao.passo
    x = (longitude - previsao.lon_oeste) / previsao.passo
    if not (0 <= y <= previsao.linhas - 1 and 0 <= x <= previsao.colunas - 1):
        return None
    i, j = min(int(y), previsao.linhas - 2), min(int(x), previsao.colunas - 2)
    fy, fx = y - i, x - j
    pesos = np.array([(1 - fy) * (1 - fx), (1 - fy) * fx, fy * (1 - fx), fy * fx])
    cantos = np.stack([valores[:, i, j], valores[:, i, j + 1], valores[:, i + 1, j], valores[:, i + 1, j + 1]], axis=1)
    validos = ~np.isnan(cantos)
    soma_pesos = (pesos * validos).sum(axis=1)
    if not soma_pesos.all():
        return None
    return (np.nan_to_num(cantos) * pesos).sum(axis=1) / soma_pesos


FATOR_DO_MAPA = 4  # a camada refina a grade de 0,4° para 0,1°, como a do MERGE


def _refinar(campo, fator):
    """Interpolação bilinear da grade em `fator` vezes mais células por lado (mesmos cantos)."""
    import numpy as np

    linhas, colunas = campo.shape
    x = np.linspace(0, colunas - 1, (colunas - 1) * fator + 1)
    y = np.linspace(0, linhas - 1, (linhas - 1) * fator + 1)
    por_linha = np.array([np.interp(x, np.arange(colunas), linha) for linha in campo])
    return np.array([np.interp(y, np.arange(linhas), coluna) for coluna in por_linha.T]).T


class Prevista(GradeNoMapa):
    """Chuva prevista somada nas próximas `dias` × 24 h, refinada para o mapa."""

    def __init__(self, dias, agora=None):
        import numpy as np

        self.dias = dias
        self.rodada = self.inicio = self.fim = None
        proximas = proximas_horas(agora)
        if proximas is None:
            return
        previsao, valores, inicio = proximas
        self.rodada = previsao.rodada
        self.inicio = previsao.primeira_hora + timedelta(hours=inicio - 1)  # começo da primeira hora somada
        self.fim = self.inicio + timedelta(hours=24 * dias)
        soma = np.nansum(valores[inicio:inicio + 24 * dias], axis=0)
        self._usar(_refinar(soma, FATOR_DO_MAPA), previsao.lat_sul, previsao.lon_oeste, previsao.passo / FATOR_DO_MAPA)


# Máxima prevista (°C, limite inferior) e suas cores; abaixo de 28 °C, transparente.
CLASSES_TEMPERATURA = [
    (28, '#fff3bf'), (30, '#ffe066'), (32, '#ffc078'), (34, '#ff922b'), (36, '#f76707'),
    (38, '#e03131'), (40, '#c2255c'), (42, '#862e9c'), (44, '#4a1486'),
]


class MaximaPrevista(GradeNoMapa):
    """Maior temperatura prevista nas próximas `dias` × 24 h (°C), refinada para o mapa."""

    classes = CLASSES_TEMPERATURA

    def __init__(self, dias, agora=None):
        import numpy as np

        self.dias = dias
        self.rodada = self.inicio = self.fim = None
        proximas = proximas_horas(agora, PrevisaoTemperatura)
        if proximas is None:
            return
        previsao, valores, inicio = proximas
        self.rodada = previsao.rodada
        self.inicio = previsao.primeira_hora + timedelta(hours=inicio - 1)
        self.fim = self.inicio + timedelta(hours=24 * dias)
        maxima = np.nanmax(valores[inicio:inicio + 24 * dias], axis=0)
        self._usar(_refinar(maxima, FATOR_DO_MAPA), previsao.lat_sul, previsao.lon_oeste, previsao.passo / FATOR_DO_MAPA)


def proximas_horas(agora=None, modelo=PrevisaoChuva):
    """(previsão, valores, índice da primeira hora depois de agora) da rodada mais recente
    que ainda cobre as próximas 72 h, ou None. `modelo`: PrevisaoChuva ou PrevisaoTemperatura."""
    agora = agora or datetime.now(timezone.utc)
    previsao = modelo.objects.first()
    if not previsao:
        return None
    inicio = max(0, int((agora - previsao.primeira_hora) / timedelta(hours=1)) + 1)
    if inicio + HORAS > previsao.horas:
        return None
    return previsao, previsao.valores(), inicio
