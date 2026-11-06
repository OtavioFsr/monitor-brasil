"""Geometria em Python puro: UF de um ponto e centro aproximado de polígonos.

A malha das UFs (IBGE, qualidade mínima) fica em dados/static/dados/brasil_ufs.geojson.
Com GeoDjango/PostGIS isso viraria uma consulta espacial; aqui basta um teste
de ponto-no-polígono por ray casting, com pré-filtro por retângulo envolvente.
"""

import json
from functools import lru_cache
from pathlib import Path

from .ufs import SIGLA_POR_CODIGO

MALHA_UFS = Path(__file__).resolve().parent.parent / 'static' / 'dados' / 'brasil_ufs.geojson'


def _poligonos(geometria):
    """Lista de polígonos (cada um: lista de anéis) de um Polygon ou MultiPolygon."""
    tipo = geometria.get('type')
    if tipo == 'Polygon':
        return [geometria['coordinates']]
    if tipo == 'MultiPolygon':
        return list(geometria['coordinates'])
    return []


def _caixa(aneis):
    xs = [p[0] for p in aneis[0]]
    ys = [p[1] for p in aneis[0]]
    return min(xs), min(ys), max(xs), max(ys)


def _dentro_do_anel(lon, lat, anel):
    dentro = False
    j = len(anel) - 1
    for i in range(len(anel)):
        xi, yi = anel[i][0], anel[i][1]
        xj, yj = anel[j][0], anel[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            dentro = not dentro
        j = i
    return dentro


def _dentro_do_poligono(lon, lat, aneis):
    """Dentro do anel externo e fora de todos os buracos."""
    if not _dentro_do_anel(lon, lat, aneis[0]):
        return False
    return not any(_dentro_do_anel(lon, lat, buraco) for buraco in aneis[1:])


@lru_cache(maxsize=1)
def _malha():
    dados = json.loads(MALHA_UFS.read_text(encoding='utf-8'))
    malha = []
    for feature in dados['features']:
        sigla = SIGLA_POR_CODIGO.get(str(feature['properties'].get('codarea')))
        for aneis in _poligonos(feature['geometry']):
            malha.append((sigla, _caixa(aneis), aneis))
    return malha


def uf_do_ponto(latitude, longitude):
    """Sigla da UF que contém o ponto, ou '' fora do território brasileiro."""
    for sigla, (x0, y0, x1, y1), aneis in _malha():
        if x0 <= longitude <= x1 and y0 <= latitude <= y1 and _dentro_do_poligono(
            longitude, latitude, aneis
        ):
            return sigla
    return ''


def limites_do_brasil():
    """Retângulo que envolve todas as UFs: (lon_min, lat_min, lon_max, lat_max)."""
    caixas = [caixa for _, caixa, _ in _malha()]
    return (
        min(c[0] for c in caixas), min(c[1] for c in caixas),
        max(c[2] for c in caixas), max(c[3] for c in caixas),
    )


@lru_cache(maxsize=4)
def mascara_ufs(lat_sul, lon_oeste, passo, linhas, colunas):
    """Grade (linhas × colunas, do sul para o norte) com o índice da UF de cada célula
    em SIGLAS_DA_MASCARA, ou 0 fora do Brasil. Os polígonos são rasterizados com o
    Pillow, o que leva milissegundos em vez de um ponto-no-polígono por célula."""
    import numpy as np
    from PIL import Image, ImageDraw

    def pixel(ponto):
        # Centro da célula (i, j) = (lat_sul + i*passo, lon_oeste + j*passo); no Pillow o pixel j
        # também tem centro na coordenada j.
        return ((ponto[0] - lon_oeste) / passo, (ponto[1] - lat_sul) / passo)

    mascara = np.zeros((linhas, colunas), dtype=np.uint8)
    for sigla, _, aneis in _malha():
        if not sigla:
            continue
        poligono = Image.new('1', (colunas, linhas), 0)
        desenho = ImageDraw.Draw(poligono)
        desenho.polygon([pixel(p) for p in aneis[0]], fill=1)
        for buraco in aneis[1:]:  # o DF é um buraco no polígono de GO
            desenho.polygon([pixel(p) for p in buraco], fill=0)
        mascara[np.asarray(poligono, dtype=bool)] = SIGLAS_DA_MASCARA.index(sigla)
    return mascara


SIGLAS_DA_MASCARA = [''] + sorted(SIGLA_POR_CODIGO.values())


def centro_aproximado(geometria):
    """Média dos vértices do anel externo do maior polígono: (lat, lon).

    Não é o centroide geométrico, mas cai dentro ou junto de polígonos
    pequenos como os do DETER, o que basta para filtrar e posicionar.
    """
    poligonos = _poligonos(geometria or {})
    if not poligonos:
        return None
    maior = max(poligonos, key=lambda aneis: len(aneis[0]))
    externo = maior[0]
    lon = sum(p[0] for p in externo) / len(externo)
    lat = sum(p[1] for p in externo) / len(externo)
    return lat, lon
