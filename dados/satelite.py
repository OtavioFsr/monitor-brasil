"""Imagens do satélite GOES-19 (canal 13, infravermelho) publicadas pelo INPE/CPTEC.

A Divisão de Satélites do INPE publica, a cada 10 min, uma pirâmide de tiles PNG
(zoom 0 a 8, numeração TMS) com o infravermelho em tons de cinza: quanto mais branco,
mais frio o topo da nuvem. O navegador busca os tiles direto do INPE (que libera
CORS) e pinta só os topos frios; aqui só descobrimos quais horários existem. O INPE
guarda cerca de 3 h de imagens, e a mais recente sai uns 25 min depois do horário.
"""

import math
import re
from datetime import datetime, timezone

from django.core.cache import cache

from .coletores.http import ErroDeColeta, baixar

URL_TILES = 'https://ftp.cptec.inpe.br/goes/goes19/goes19_web/tiles/S11161113_tiles/'
ZOOM_MAXIMO = 8
ZOOM_MINIMO_DO_MAPA = 4  # o minZoom do Leaflet em mapa.js
# Extensão do produto (graus): fora dela o INPE não tem tiles.
LIMITES = [[-56.0, -116.0], [35.0, -25.0]]
CHAVE_CACHE = 'satelite:horarios'
CACHE_SEGUNDOS = 180

# Tons de cinza → temperatura do topo da nuvem, calibrados comparando os tiles com o
# NetCDF do mesmo horário (canal 13, Kelvin): ~2 tons por °C, -20 °C ≈ cinza 157,
# e o branco (255) satura por volta de -70 °C. Precisão de uns ±3 °C.
def cinza_da_temperatura(celsius):
    return max(0, min(255, round(157 - 2 * (celsius + 20))))


# Classes da camada (temperatura do topo, °C, limite superior), cor e opacidade (0–255).
# Mais frio = nuvem mais alta e, em geral, mais carregada; abaixo de -60 °C costuma ser tempestade.
CLASSES = [
    (-30, '#e9ecef', 90),
    (-40, '#adb5bd', 140),
    (-50, '#ffd43b', 190),
    (-60, '#ff922b', 210),
    (-65, '#f03e3e', 225),
    (-70, '#ae3ec9', 235),
]


def classes_para_o_mapa():
    return [
        {'celsius': celsius, 'cinza': cinza_da_temperatura(celsius), 'cor': cor, 'opacidade': opacidade}
        for celsius, cor, opacidade in CLASSES
    ]


def interpretar_listagem(html):
    """Horários (YYYYMMDDHHMM, UTC) da listagem de diretórios do INPE, do mais antigo ao mais recente."""
    return sorted(set(re.findall(r'href="(\d{12})/"', html)))


def tile_de_conferencia(latitude=-15.8, longitude=-47.9, zoom=ZOOM_MAXIMO):
    """(z, x, y TMS) do tile que contém o ponto (padrão: Brasília, no zoom mais alto)."""
    n = 2 ** zoom
    x = int((longitude + 180) / 360 * n)
    phi = math.radians(latitude)
    y_xyz = int((1 - math.asinh(math.tan(phi)) / math.pi) / 2 * n)
    return zoom, x, n - 1 - y_xyz


def _completo(horario):
    """O INPE cria a pasta antes de terminar de gravar os tiles, e não grava os zooms juntos
    (em 31/10/2026 o zoom 8 já existia e o 4 ainda não): confere um tile do zoom mais alto
    e um do mais baixo que o mapa usa."""
    try:
        for zoom in (ZOOM_MAXIMO, ZOOM_MINIMO_DO_MAPA):
            z, x, y = tile_de_conferencia(zoom=zoom)
            baixar(f'{URL_TILES}{horario}/{z}/{x}/{y}.png', tentativas=1, timeout=20)
        return True
    except ErroDeColeta:
        return False


def horarios_disponiveis():
    """{'horarios': [...], 'atual': horário mais recente completo}. Levanta ErroDeColeta."""
    em_cache = cache.get(CHAVE_CACHE)
    if em_cache:
        return em_cache
    horarios = interpretar_listagem(baixar(URL_TILES, timeout=30).text)
    if not horarios:
        raise ErroDeColeta('a listagem de imagens do INPE veio vazia')
    atual = horarios[-1]
    if len(horarios) > 1 and not _completo(atual):
        atual = horarios[-2]
    resultado = {'horarios': horarios[:horarios.index(atual) + 1], 'atual': atual}
    cache.set(CHAVE_CACHE, resultado, CACHE_SEGUNDOS)
    return resultado


def como_datetime(horario):
    return datetime.strptime(horario, '%Y%m%d%H%M').replace(tzinfo=timezone.utc)


def temperatura_do_cinza(cinza):
    """Inverso de cinza_da_temperatura (°C). O branco (255) satura: "-69 °C ou mais frio"."""
    return -20 - (cinza - 157) / 2


def _distancia_km(lat1, lon1, lat2, lon2):
    raio = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * raio * math.asin(math.sqrt(a))


def nuvem_mais_fria_perto(latitude, longitude, raio_km, horario=None, zoom=ZOOM_MAXIMO):
    """Topo de nuvem mais frio a até `raio_km` do ponto, na imagem mais recente do GOES-19:
    {'celsius', 'distancia_km', 'horario'}; celsius None se não houver nuvem alta (cinza < -20 °C).
    Lê os tiles do zoom mais alto (pixel de ~550 m) que cobrem o círculo. Levanta ErroDeColeta."""
    import io

    from PIL import Image

    horario = horario or horarios_disponiveis()['atual']
    n = 2 ** zoom
    tamanho = 256 * n

    def global_px(lat, lon):
        x = (lon + 180) / 360 * tamanho
        y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * tamanho
        return x, y

    cx, cy = global_px(latitude, longitude)
    metros_por_pixel = 2 * math.pi * 6_378_137 * math.cos(math.radians(latitude)) / tamanho
    raio_px = raio_km * 1000 / metros_por_pixel
    tiles = {(int(x // 256), int(y // 256))
             for x in (cx - raio_px, cx + raio_px) for y in (cy - raio_px, cy + raio_px)}
    mais_fria = None
    for tx, ty in sorted(tiles):
        chave = f'satelite:tile:{horario}:{zoom}:{tx}:{ty}'
        conteudo = cache.get(chave)
        if conteudo is None:
            conteudo = baixar(f'{URL_TILES}{horario}/{zoom}/{tx}/{n - 1 - ty}.png', tentativas=2, timeout=30).content
            cache.set(chave, conteudo, CACHE_SEGUNDOS * 10)
        imagem = Image.open(io.BytesIO(conteudo)).convert('LA')
        cinzas, alfas = imagem.getchannel('L').load(), imagem.getchannel('A').load()
        for py in range(256):
            for px in range(256):
                gx, gy = tx * 256 + px + 0.5, ty * 256 + py + 0.5
                if (gx - cx) ** 2 + (gy - cy) ** 2 > raio_px ** 2 or not alfas[px, py]:
                    continue
                if mais_fria is None or cinzas[px, py] > mais_fria[0]:
                    mais_fria = (cinzas[px, py], gx, gy)
    if mais_fria is None or mais_fria[0] < cinza_da_temperatura(-20):
        return {'celsius': None, 'distancia_km': None, 'horario': horario}
    cinza, gx, gy = mais_fria
    lon = gx / tamanho * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * gy / tamanho))))
    return {
        'celsius': round(temperatura_do_cinza(cinza)),
        'distancia_km': round(_distancia_km(latitude, longitude, lat, lon), 1),
        'horario': horario,
    }
