"""Vento da previsão horária do modelo Eta (INPE/CPTEC, 40 km): a 10 m e em 500 hPa.

O CPTEC roda o Eta sobre a América do Sul e publica um GRIB1 por hora de previsão
(~12 MB). As mensagens seguem a ordem das variáveis do .ctl, com numeração própria:
o parâmetro 9 é o vento zonal a 10 m (u, para leste) e o 10 o meridional (v, para
norte), no começo do arquivo; 56 e 57 são u e v nos 22 níveis de pressão, no meio dele.
Por isso baixamos só o trecho de cada camada (Range) e, se não bastar, o arquivo
inteiro. O vento a 10 m desenha as setas; o de 500 hPa (~5,5 km), que costuma conduzir
as nuvens de tempestade, move a projeção das nuvens. Nada vai para o banco: a grade da
hora fica em cache por 30 min, como as imagens de satélite.
"""

import threading
from datetime import datetime, timedelta, timezone

from django.core.cache import cache

from .coletores.geo import limites_do_brasil
from .coletores.http import ErroDeColeta, baixar

URL_HORA = (
    'https://ftp.cptec.inpe.br/modelos/tempo/Eta/ams_40km/brutos/'
    '{rodada:%Y/%m/%d/%H}/eta_40km_{rodada:%Y%m%d%H}+{valido:%Y%m%d%H}.grb'
)
# Camada → parâmetros u e v, nível de pressão (None = superfície) e trecho do arquivo onde
# ficam (bytes; medido nos arquivos de out/2026, com folga: u500 em ~3,8 MB, v500 em ~4,8 MB).
CAMADAS = {
    '10m': {'u': 9, 'v': 10, 'nivel': None, 'bytes': (0, 1024 * 1024 - 1), 'nome': 'vento a 10 m'},
    '500hPa': {'u': 56, 'v': 57, 'nivel': 500, 'bytes': (3_500_000, 5_300_000), 'nome': 'vento em 500 hPa'},
}
PARAMETRO_U, PARAMETRO_V = CAMADAS['10m']['u'], CAMADAS['10m']['v']
# Classes das setas (km/h, limite inferior) e suas cores.
CLASSES = [(0, '#66d9e8'), (10, '#22b8cf'), (20, '#1098ad'), (30, '#0b7285'), (45, '#0b3d5c')]
MARGEM_GRAUS = 3.0
CACHE_SEGUNDOS = 30 * 60


# O eccodes não aguenta duas threads lendo GRIB ao mesmo tempo (o runserver atende em
# paralelo: o painel de situação e a projeção das nuvens pedem vento juntos). Sem a trava
# o processo cai com falha de segmentação.
_TRAVA_ECCODES = threading.Lock()


def ler_campos(conteudo, parametros, nivel=None, limites=None):
    """GRIB1 do Eta (inteiro ou um trecho, que pode começar no meio de uma mensagem) →
    (geometria, {parâmetro: matriz}) só com os parâmetros pedidos que estiverem no trecho.
    As matrizes vêm recortadas em volta do Brasil, com linhas do sul para o norte e NaN
    onde o Eta não tem dado; a geometria é {'lat_sul', 'lon_oeste', 'passo'} do recorte.
    """
    with _TRAVA_ECCODES:
        return _ler_campos(conteudo, parametros, nivel, limites)


def _ler_campos(conteudo, parametros, nivel, limites):
    import eccodes
    import numpy as np

    campos, geometria = {}, None
    posicao = conteudo.find(b'GRIB')
    while posicao >= 0 and len(campos) < len(parametros):
        # Mensagem cortada no fim do trecho, ou um "GRIB" no meio dos dados de outra: pula.
        proxima = conteudo.find(b'GRIB', posicao + 4)
        try:
            mensagem = eccodes.codes_new_from_message(conteudo[posicao:])
        except eccodes.CodesInternalError:
            posicao = proxima
            continue
        try:
            tamanho = eccodes.codes_get(mensagem, 'totalLength')
            parametro = eccodes.codes_get(mensagem, 'indicatorOfParameter')
            no_nivel = nivel is None or eccodes.codes_get(mensagem, 'level') == nivel
        except eccodes.CodesInternalError:
            eccodes.codes_release(mensagem)
            posicao = proxima
            continue
        if posicao + tamanho > len(conteudo):
            eccodes.codes_release(mensagem)
            posicao = proxima
            continue
        try:
            if parametro in parametros and no_nivel:
                ler = lambda chave: eccodes.codes_get(mensagem, chave)  # noqa: E731
                ni, nj = ler('Ni'), ler('Nj')
                valores = eccodes.codes_get_values(mensagem)
                if ler('bitmapPresent'):  # a chuva vem com máscara: 9999 onde não há dado
                    valores[valores == ler('missingValue')] = np.nan
                valores = valores.reshape(nj, ni)
                if not ler('jScansPositively'):
                    valores = valores[::-1]
                campos[parametro] = valores
                lon_oeste = ler('longitudeOfFirstGridPointInDegrees')
                geometria = (
                    min(ler('latitudeOfFirstGridPointInDegrees'), ler('latitudeOfLastGridPointInDegrees')),
                    lon_oeste - 360 if lon_oeste > 180 else lon_oeste,
                    ler('iDirectionIncrementInDegrees'),
                )
        except eccodes.CodesInternalError as erro:
            raise ErroDeColeta(f'GRIB do Eta ilegível: {erro}') from erro
        finally:
            eccodes.codes_release(mensagem)
        posicao = conteudo.find(b'GRIB', posicao + max(tamanho, 4))
    if not campos:
        return None, {}

    lat_sul, lon_oeste, passo = geometria
    lon_min, lat_min, lon_max, lat_max = limites or limites_do_brasil()
    nj, ni = next(iter(campos.values())).shape
    i0 = max(0, int((lat_min - MARGEM_GRAUS - lat_sul) // passo))
    i1 = min(nj, int((lat_max + MARGEM_GRAUS - lat_sul) // passo) + 2)
    j0 = max(0, int((lon_min - MARGEM_GRAUS - lon_oeste) // passo))
    j1 = min(ni, int((lon_max + MARGEM_GRAUS - lon_oeste) // passo) + 2)
    geometria = {'lat_sul': round(lat_sul + i0 * passo, 4), 'lon_oeste': round(lon_oeste + j0 * passo, 4), 'passo': passo}
    return geometria, {p: campo[i0:i1, j0:j1] for p, campo in campos.items()}


def interpretar(conteudo, limites=None, camada='10m'):
    """GRIB1 (inteiro ou um trecho) → dict com u, v (m/s, listas de linhas do sul para
    o norte) e a geometria da grade recortada. Levanta ErroDeColeta se faltar u ou v.
    """
    import numpy as np

    definicao = CAMADAS[camada]
    geometria, campos = ler_campos(conteudo, (definicao['u'], definicao['v']), definicao['nivel'], limites)
    if len(campos) < 2:
        raise ErroDeColeta(f'o {definicao["nome"]} não está no trecho baixado do Eta')
    u, v = np.round(campos[definicao['u']], 1), np.round(campos[definicao['v']], 1)
    return {**geometria, 'linhas': int(u.shape[0]), 'colunas': int(u.shape[1]), 'u': u.tolist(), 'v': v.tolist()}


def rodadas_candidatas(valido):
    """Rodadas (00 e 12 UTC) dos últimos 3 dias que começam até a hora pedida, da mais nova à mais velha."""
    dia = valido.replace(hour=0)
    candidatas = [dia - timedelta(days=d) + timedelta(hours=h) for d in range(3) for h in (12, 0)]
    return [r for r in candidatas if r <= valido]


def _baixar_hora(rodada, valido, camada='10m'):
    url = URL_HORA.format(rodada=rodada, valido=valido)
    primeiro, ultimo = CAMADAS[camada]['bytes']
    trecho = baixar(url, cabecalhos={'Range': f'bytes={primeiro}-{ultimo}'}, tentativas=2, timeout=60)
    try:
        return interpretar(trecho.content, camada=camada)
    except ErroDeColeta:
        if trecho.status_code != 206:  # o servidor já mandou o arquivo inteiro
            raise
        return interpretar(baixar(url, timeout=120).content, camada=camada)


def vento_agora(agora=None, camada='10m'):
    """Grade de vento da hora cheia atual, da rodada mais recente que a tenha. Levanta ErroDeColeta."""
    agora = agora or datetime.now(timezone.utc)
    valido = agora.replace(minute=0, second=0, microsecond=0)
    chave = f'vento:{camada}:{valido:%Y%m%d%H}'
    em_cache = cache.get(chave)
    if em_cache:
        return em_cache
    falhas = []
    for rodada in rodadas_candidatas(valido):
        try:
            grade = _baixar_hora(rodada, valido, camada)
        except ErroDeColeta as erro:
            falhas.append(f'{rodada:%d/%m %Hh}: {erro}')
            continue
        resultado = {'valido_em': valido.isoformat(), 'rodada': rodada.isoformat(), 'camada': camada, **grade}
        cache.set(chave, resultado, CACHE_SEGUNDOS)
        return resultado
    raise ErroDeColeta('nenhuma rodada do Eta tem esta hora; ' + '; '.join(falhas[:2]))
