"""Chuva diária do MERGE (INPE/CPTEC), em grade de 0,1° sobre a América do Sul.

O MERGE combina a estimativa de satélite (GPM-IMERG) com os pluviômetros do
Brasil. Cada arquivo GRIB2 traz a chuva acumulada nas 24 h que terminam às
12h UTC (9h de Brasília) do dia do nome do arquivo; o do dia sai no fim da
manhã. Guardamos só o recorte que envolve o Brasil.
"""

import zlib
from datetime import datetime, timedelta, timezone

from ..models import ChuvaDiaria
from .geo import limites_do_brasil
from .http import ColetaParcial, ErroDeColeta, baixar

URL_DIARIO = (
    'https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/'
    '{data:%Y}/{data:%m}/MERGE_CPTEC_{data:%Y%m%d}.grib2'
)

# Precipitação total no MERGE: disciplina 0, categoria 15, parâmetro 5 (o arquivo
# traz também o número de estações por célula, que ignoramos).
CATEGORIA_CHUVA, PARAMETRO_CHUVA = 15, 5
MARGEM_GRAUS = 0.3


def _recorte(eixo_inicio, passo, n, minimo, maximo):
    """Índices [i0, i1) das células cujos centros caem em [minimo, maximo]."""
    i0 = max(0, int((minimo - eixo_inicio) // passo))
    i1 = min(n, int((maximo - eixo_inicio) // passo) + 2)
    return i0, i1


def interpretar(conteudo, limites=None):
    """GRIB2 do MERGE → dict com os campos de ChuvaDiaria (a grade ainda como array).

    `limites` = (lon_min, lat_min, lon_max, lat_max) do recorte; padrão: o Brasil.
    """
    import eccodes
    import numpy as np

    lon_min, lat_min, lon_max, lat_max = limites or limites_do_brasil()
    deslocamento = 0
    while deslocamento < len(conteudo):
        try:
            mensagem = eccodes.codes_new_from_message(conteudo[deslocamento:])
        except eccodes.CodesInternalError as erro:
            raise ErroDeColeta(f'GRIB2 do MERGE ilegível: {erro}') from erro
        try:
            deslocamento += eccodes.codes_get(mensagem, 'totalLength')
            if (eccodes.codes_get(mensagem, 'parameterCategory'),
                    eccodes.codes_get(mensagem, 'parameterNumber')) != (CATEGORIA_CHUVA, PARAMETRO_CHUVA):
                continue
            ler = lambda chave: eccodes.codes_get(mensagem, chave)  # noqa: E731
            ni, nj = ler('Ni'), ler('Nj')
            valores = eccodes.codes_get_values(mensagem).astype('f4').reshape(nj, ni)
            valores[valores == ler('missingValue')] = np.nan
            lat_sul = min(ler('latitudeOfFirstGridPointInDegrees'), ler('latitudeOfLastGridPointInDegrees'))
            lon_oeste = ler('longitudeOfFirstGridPointInDegrees')
            passo = ler('iDirectionIncrementInDegrees')
            if not ler('jScansPositively'):
                valores = valores[::-1]
            data = datetime.strptime(str(ler('dataDate')), '%Y%m%d').date()
        except eccodes.CodesInternalError as erro:
            raise ErroDeColeta(f'GRIB2 do MERGE ilegível: {erro}') from erro
        finally:
            eccodes.codes_release(mensagem)

        if lon_oeste > 180:
            lon_oeste -= 360
        i0, i1 = _recorte(lat_sul, passo, nj, lat_min - MARGEM_GRAUS, lat_max + MARGEM_GRAUS)
        j0, j1 = _recorte(lon_oeste, passo, ni, lon_min - MARGEM_GRAUS, lon_max + MARGEM_GRAUS)
        if i1 <= i0 or j1 <= j0:
            raise ErroDeColeta('a grade do MERGE não cobre o Brasil')
        grade = np.ascontiguousarray(valores[i0:i1, j0:j1])
        return {
            'data': data,
            'lat_sul': round(lat_sul + i0 * passo, 4),
            'lon_oeste': round(lon_oeste + j0 * passo, 4),
            'passo': passo,
            'linhas': grade.shape[0],
            'colunas': grade.shape[1],
            'grade': grade,
        }
    raise ErroDeColeta('o arquivo do MERGE não tem o campo de precipitação')


def gravar(dia):
    """Grava (ou substitui) um dia interpretado."""
    campos = dict(dia, grade=zlib.compress(dia['grade'].astype('<f4').tobytes(), 6))
    ChuvaDiaria.objects.update_or_create(data=campos.pop('data'), defaults=campos)


def datas_a_buscar(hoje, dias):
    """Os `dias` anteriores e hoje, do mais antigo ao mais recente."""
    return [hoje - timedelta(days=n) for n in range(dias, -1, -1)]


def coletar(agora=None, dias=3):
    """Baixa os dias que ainda não estão no banco, dentro dos últimos `dias`. Devolve quantos gravou.

    O arquivo de hoje só existe a partir do fim da manhã; até lá a falha dele é esperada.
    """
    agora = agora or datetime.now(timezone.utc)
    hoje = agora.date()
    ja_gravados = set(ChuvaDiaria.objects.filter(data__gte=hoje - timedelta(days=dias))
                      .values_list('data', flat=True))
    faltando = [d for d in datas_a_buscar(hoje, dias) if d not in ja_gravados]
    gravados, falhas = 0, []
    for data in faltando:
        try:
            resposta = baixar(URL_DIARIO.format(data=data))
            gravar(interpretar(resposta.content))
            gravados += 1
        except ErroDeColeta as erro:
            if data != hoje:
                falhas.append(f'{data:%d/%m}: {erro}')
    if falhas and not gravados:
        raise ErroDeColeta('; '.join(falhas))
    if falhas:
        raise ColetaParcial(gravados, falhas)
    return gravados


def ultimo_dia():
    """Data mais recente gravada, ou None."""
    return ChuvaDiaria.objects.order_by('-data').values_list('data', flat=True).first()
