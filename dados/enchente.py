"""Simulador de enchente do Guaíba: relevo (ANADEM) e nível do rio (ANA).

O relevo vem do ANADEM, o modelo digital de terreno da ANA com o IPH/UFRGS (sem
prédios nem vegetação, 30 m), no recorte das bacias do Guaíba publicado no SNIRH.
O arquivo inteiro tem 773 MB, sem compressão e com uma linha por bloco; o comando
`preparar_enchente` baixa só a janela em volta de Porto Alegre, linha a linha, e a
reprojeta para a grade do mapa. A simulação em si (o "rio subindo") roda no
navegador: a partir do Guaíba, alaga o terreno conectado abaixo do nível escolhido.

O nível vem da estação telemétrica Cais Mauá C6 da ANA (87450004), em centímetros.
O zero da régua é o nível médio do mar (marco IBGE 1788A), a mesma referência
aproximada das altitudes do ANADEM; por isso o nível pode ser comparado direto com
o terreno, com diferença de alguns decímetros entre as duas referências.
"""

import math
import struct
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.cache import cache

from .coletores.http import ErroDeColeta, baixar

URL_ANADEM = 'https://metadados.snirh.gov.br/files/a5b8f184-d4e3-45e6-a62b-e76d1f255f55/anadem.tif'
URL_TELEMETRIA = 'https://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos'
ESTACAO_CAIS_MAUA = '87450004'
TENTATIVAS_SEM_LEITURA = 3

# Banco de dados das cheias de maio de 2024 do IPH/UFRGS (Zenodo, CC-BY 4.0): manchas de
# inundação simuladas para Porto Alegre em vários níveis, a mancha observada por satélite
# em 06/05/2024 e o traçado dos diques. No centro de Porto Alegre o ANADEM fica alto demais
# (sobram prédios no modelo: o Mercado Público sai com 13 m), então lá valem as manchas do IPH.
URL_CHEIAS_2024 = 'https://zenodo.org/records/13227745/files/cheias_rhguaiba_2024_db_v12.gpkg?download=1'
CAMADAS_IPH = {  # camada → nível no Cais Mauá (m)
    'poa_inundacao_2015_294cm': 2.94, 'poa_inundacao_1967_313cm': 3.13, 'poa_inundacao_2023_set_318cm': 3.18,
    'poa_inundacao_2023_nov_346cm': 3.46, 'poa_inundacao_1941_475cm': 4.75, 'poa_inundacao_simulada_500cm': 5.00,
    'poa_inundacao_2024_530cm': 5.30, 'poa_inundacao_simulada_550cm': 5.50, 'poa_inundacao_simulada_600cm': 6.00,
    'poa_inundacao_simulada_650cm': 6.50, 'poa_inundacao_simulada_700cm': 7.00,
}
CAMADA_OBSERVADA = 'rhguaiba_planet-skysat_inundacao_obs_20240506'
CAMADA_DIQUES = 'rmpa_sb_drenagem_diques'

PASTA = Path(__file__).resolve().parent / 'static' / 'dados' / 'enchente'
ARQUIVO_RELEVO = PASTA / 'relevo_guaiba.bin'          # int16, decímetros; NODATA fora do ANADEM
ARQUIVO_ALCANCE = PASTA / 'alcance_guaiba.bin'        # int16, decímetros: nível do Guaíba em que a água chega
ARQUIVO_MUNICIPIOS = PASTA / 'municipios_guaiba.bin'  # uint8, índice em `municipios` do JSON (0 = nenhum)
ARQUIVO_OBSERVADO = PASTA / 'observado_2024.bin'      # uint8, 1 = alagado em 06/05/2024 (satélite)
ARQUIVO_DIQUES = PASTA / 'diques.geojson'
ARQUIVO_META = PASTA / 'relevo_guaiba.json'
NODATA = -32768
SEM_ALCANCE = 32767

# Janela: Porto Alegre, o Delta do Jacuí, Canoas, Eldorado do Sul e o norte do Guaíba.
JANELA = {'oeste': -51.66, 'leste': -50.95, 'sul': -30.30, 'norte': -29.78}
RESOLUCAO_M = 85  # no terreno; ~3 × 3 pixels do ANADEM
# Pontos no meio do Guaíba (no ANADEM a superfície da água vale 0 m): a água parte daqui.
SEMENTES = [(-30.25, -51.27), (-30.15, -51.30), (-30.08, -51.27), (-30.03, -51.24)]
MUNICIPIOS = [
    'Porto Alegre', 'Canoas', 'Eldorado do Sul', 'Guaíba', 'Nova Santa Rita', 'Esteio',
    'Sapucaia do Sul', 'Cachoeirinha', 'Gravataí', 'Alvorada', 'Viamão', 'Triunfo',
    'Charqueadas', 'Barra do Ribeiro',
]

# Marcas do slider (metros na régua do Cais Mauá). Alerta e inundação: Defesa Civil do RS;
# 2024: pico revisado pelo SGB (5,37 m); a faixa "normal" (0,80 a 1,30 m) é da Prefeitura.
REFERENCIAS = [
    {'metros': 2.55, 'nome': 'Alerta'},
    {'metros': 3.00, 'nome': 'Inundação'},
    {'metros': 4.76, 'nome': '1941'},
    {'metros': 5.37, 'nome': 'Maio de 2024'},
]
# O impacto é medido acima do topo da faixa normal: abaixo dele ficam várzeas e ilhas
# que vivem alagadas.
NIVEL_NORMAL = 1.30
NIVEL_MAXIMO = 7.0

HORARIO_DE_BRASILIA = ZoneInfo('America/Sao_Paulo')


# ---- Projeções -------------------------------------------------------------
# O ANADEM está em "South America Albers Equal Area Conic" sobre o SAD69.

WGS84 = (6378137.0, 1 / 298.257223563)
SAD69 = (6378160.0, 1 / 298.25)
# SAD69 → SIRGAS2000/WGS84 (IBGE, R.PR 1/2005), em metros.
SAD69_PARA_WGS84 = (-67.348, 3.879, -38.223)
ALBERS = {'paralelo1': -5.0, 'paralelo2': -42.0, 'lat_origem': -32.0, 'lon_central': -60.0}


def _geocentrico(lat, lon, elipsoide):
    import numpy as np

    a, f = elipsoide
    e2 = f * (2 - f)
    phi, lam = np.radians(lat), np.radians(lon)
    n = a / np.sqrt(1 - e2 * np.sin(phi) ** 2)
    return n * np.cos(phi) * np.cos(lam), n * np.cos(phi) * np.sin(lam), n * (1 - e2) * np.sin(phi)


def _geodesico(x, y, z, elipsoide):
    import numpy as np

    a, f = elipsoide
    e2 = f * (2 - f)
    p = np.hypot(x, y)
    phi = np.arctan2(z, p * (1 - e2))
    for _ in range(5):
        n = a / np.sqrt(1 - e2 * np.sin(phi) ** 2)
        phi = np.arctan2(z + e2 * n * np.sin(phi), p)
    return np.degrees(phi), np.degrees(np.arctan2(y, x))


def wgs84_para_sad69(lat, lon):
    x, y, z = _geocentrico(lat, lon, WGS84)
    dx, dy, dz = SAD69_PARA_WGS84
    return _geodesico(x - dx, y - dy, z - dz, SAD69)


def albers(lat, lon, elipsoide=SAD69, parametros=ALBERS):
    """Albers cônica equivalente sobre o elipsoide (Snyder, 1987, eq. 14-1 a 14-12): (x, y) em metros."""
    import numpy as np

    a, f = elipsoide
    e = math.sqrt(f * (2 - f))

    def q(phi):
        s = np.sin(phi)
        return (1 - e * e) * (s / (1 - e * e * s * s) - np.log((1 - e * s) / (1 + e * s)) / (2 * e))

    def m(phi):
        return np.cos(phi) / np.sqrt(1 - e * e * np.sin(phi) ** 2)

    p1, p2 = math.radians(parametros['paralelo1']), math.radians(parametros['paralelo2'])
    p0, l0 = math.radians(parametros['lat_origem']), math.radians(parametros['lon_central'])
    n = (m(p1) ** 2 - m(p2) ** 2) / (q(p2) - q(p1))
    c = m(p1) ** 2 + n * q(p1)
    rho0 = a * np.sqrt(c - n * q(p0)) / n
    rho = a * np.sqrt(c - n * q(np.radians(lat))) / n
    theta = n * (np.radians(lon) - l0)
    return rho * np.sin(theta), rho0 - rho * np.cos(theta)


def mercator_y(lat):
    import numpy as np

    return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def latitude_de_mercator(y):
    import numpy as np

    return np.degrees(2 * np.arctan(np.exp(y)) - np.pi / 2)


def utm_para_latlon(x, y, zona=22, sul=True, elipsoide=WGS84):
    """UTM → (lat, lon) em graus (Snyder, 1987, eq. 8-18 a 8-25). SIRGAS 2000 ≈ WGS84."""
    import numpy as np

    a, f = elipsoide
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    k0 = 0.9996
    x = np.asarray(x, dtype=float) - 500_000
    y = np.asarray(y, dtype=float) - (10_000_000 if sul else 0)
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1**3 / 32) * np.sin(2 * mu) + (21 * e1**2 / 16 - 55 * e1**4 / 32) * np.sin(4 * mu)
            + (151 * e1**3 / 96) * np.sin(6 * mu) + (1097 * e1**4 / 512) * np.sin(8 * mu))
    c1 = ep2 * np.cos(phi1) ** 2
    t1 = np.tan(phi1) ** 2
    n1 = a / np.sqrt(1 - e2 * np.sin(phi1) ** 2)
    r1 = a * (1 - e2) / (1 - e2 * np.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = phi1 - (n1 * np.tan(phi1) / r1) * (
        d**2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720)
    lon = (d - (1 + 2 * t1 + c1) * d**3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120) / np.cos(phi1)
    return np.degrees(lat), (zona * 6 - 183) + np.degrees(lon)


# ---- GeoPackage (banco das cheias de 2024) ---------------------------------

def geometria_gpkg(blob):
    """Geometria de um GeoPackage → lista de partes: polígonos como [anel, ...] e linhas como
    [[(x, y), ...]] (cada anel/linha é uma lista de (x, y)). Aceita WKB 2D, Z e M."""
    if blob[:2] != b'GP':
        raise ValueError('geometria GeoPackage inválida')
    bandeiras = blob[3]
    envelope = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(bandeiras >> 1) & 0b111]
    return _wkb(memoryview(blob)[8 + envelope:])[0]


def _wkb(dados):
    ordem = '<' if dados[0] == 1 else '>'
    tipo = struct.unpack_from(ordem + 'I', dados, 1)[0]
    base, dimensoes = tipo % 1000, 2 + (tipo // 1000 in (1, 2)) + (tipo // 1000 == 3) * 2
    pos = 5

    def pontos(pos):
        n = struct.unpack_from(ordem + 'I', dados, pos)[0]
        valores = struct.unpack_from(ordem + 'd' * n * dimensoes, dados, pos + 4)
        return [(valores[i], valores[i + 1]) for i in range(0, len(valores), dimensoes)], pos + 4 + 8 * n * dimensoes

    if base == 2:  # LineString
        linha, pos = pontos(pos)
        return [[linha]], pos
    if base == 3:  # Polygon
        n = struct.unpack_from(ordem + 'I', dados, pos)[0]
        pos += 4
        aneis = []
        for _ in range(n):
            anel, pos = pontos(pos)
            aneis.append(anel)
        return [aneis], pos
    if base in (5, 6):  # MultiLineString, MultiPolygon
        n = struct.unpack_from(ordem + 'I', dados, pos)[0]
        pos += 4
        partes = []
        for _ in range(n):
            sub, tamanho = _wkb(dados[pos:])
            partes.extend(sub)
            pos += tamanho
        return partes, pos
    raise ValueError(f'tipo WKB {tipo} não suportado')


def ler_camada_gpkg(conexao, camada):
    """Todas as partes de uma camada do GeoPackage, no sistema de coordenadas dela."""
    coluna = conexao.execute(
        'select column_name from gpkg_geometry_columns where table_name = ?', (camada,)
    ).fetchone()[0]
    partes = []
    for (blob,) in conexao.execute(f'select "{coluna}" from "{camada}" where "{coluna}" is not null'):
        partes.extend(geometria_gpkg(blob))
    return partes


def rasterizar(poligonos_lonlat, linhas, colunas, limites):
    """Polígonos (anéis em lon/lat) → máscara booleana na grade de saída (Web Mercator)."""
    import numpy as np
    from PIL import Image, ImageDraw

    y_norte, y_sul = mercator_y(limites[1][0]), mercator_y(limites[0][0])
    oeste, leste = limites[0][1], limites[1][1]
    imagem = Image.new('1', (colunas, linhas), 0)
    desenho = ImageDraw.Draw(imagem)

    def pixels(anel):
        # No Pillow o pixel i tem centro na coordenada i; a célula i da grade vai de i a i+1.
        lon = np.array([p[0] for p in anel])
        lat = np.array([p[1] for p in anel])
        xs = (lon - oeste) / (leste - oeste) * colunas - 0.5
        ys = (y_norte - mercator_y(lat)) / (y_norte - y_sul) * linhas - 0.5
        return list(zip(xs.tolist(), ys.tolist()))

    for aneis in poligonos_lonlat:
        if len(aneis[0]) >= 3:
            desenho.polygon(pixels(aneis[0]), fill=1)
    for aneis in poligonos_lonlat:  # buracos depois de todos os contornos
        for buraco in aneis[1:]:
            if len(buraco) >= 3:
                desenho.polygon(pixels(buraco), fill=0)
    return np.asarray(imagem, dtype=bool)


def calcular_alcance(relevo_dm, sementes):
    """Nível (dm) em que a água vinda das sementes chega a cada célula: o menor "nível de
    transbordamento" no caminho (priority-flood com fila por baldes, pois o relevo é inteiro)."""
    import numpy as np

    linhas, colunas = relevo_dm.shape
    relevo = relevo_dm.ravel()
    validos = relevo != NODATA
    minimo, maximo = int(relevo[validos].min()), int(relevo[validos].max())
    alcance = np.full(relevo.shape, SEM_ALCANCE, dtype=np.int32)
    baldes = [[] for _ in range(maximo - minimo + 1)]
    for linha, coluna in sementes:
        i = linha * colunas + coluna
        alcance[i] = relevo[i]
        baldes[relevo[i] - minimo].append(i)
    relevo_l = relevo.tolist()
    alcance_l = alcance.tolist()
    for b, balde in enumerate(baldes):
        nivel = b + minimo
        k = 0
        while k < len(balde):  # o balde cresce enquanto é percorrido
            i = balde[k]
            k += 1
            if alcance_l[i] != nivel:
                continue
            l, c = divmod(i, colunas)
            for j in (i - colunas if l > 0 else -1, i + colunas if l < linhas - 1 else -1,
                      i - 1 if c > 0 else -1, i + 1 if c < colunas - 1 else -1):
                if j < 0 or relevo_l[j] == NODATA:
                    continue
                novo = max(nivel, relevo_l[j])
                if novo < alcance_l[j]:
                    alcance_l[j] = novo
                    baldes[novo - minimo].append(j)
        baldes[b] = None
    return np.array(alcance_l, dtype=np.int32).reshape(linhas, colunas)


# ---- Leitura do ANADEM -----------------------------------------------------

def ler_cabecalho_tiff(dados):
    """Tags do primeiro IFD de um TIFF clássico (little-endian) sem compressão, com uma linha por strip."""
    if dados[:4] != b'II*\x00':
        raise ErroDeColeta('o ANADEM não veio como TIFF little-endian clássico')
    tamanhos = {1: ('B', 1), 2: ('s', 1), 3: ('H', 2), 4: ('I', 4), 12: ('d', 8)}
    ifd = struct.unpack_from('<I', dados, 4)[0]
    tags = {}
    for i in range(struct.unpack_from('<H', dados, ifd)[0]):
        tag, tipo, quantidade = struct.unpack_from('<HHI', dados, ifd + 2 + 12 * i)
        if tipo not in tamanhos:
            continue
        formato, tamanho = tamanhos[tipo]
        inicio = ifd + 2 + 12 * i + 8
        if tamanho * quantidade > 4:
            inicio = struct.unpack_from('<I', dados, inicio)[0]
        if inicio + tamanho * quantidade > len(dados):
            raise ErroDeColeta('o cabeçalho do ANADEM é maior que o trecho baixado')
        if tipo == 2:
            tags[tag] = dados[inicio:inicio + quantidade].rstrip(b'\x00').decode('latin-1')
        else:
            tags[tag] = struct.unpack_from(f'<{quantidade}{formato}', dados, inicio)
    obrigatorias = {256, 257, 258, 259, 273, 278, 339, 33550, 33922}
    if not obrigatorias <= tags.keys():
        raise ErroDeColeta(f'faltam tags no TIFF do ANADEM: {sorted(obrigatorias - tags.keys())}')
    if tags[259] != (1,) or tags[258] != (32,) or tags[339] != (3,) or tags[278] != (1,):
        raise ErroDeColeta('o ANADEM mudou de formato (esperado float32 sem compressão, uma linha por strip)')
    return {
        'largura': tags[256][0],
        'altura': tags[257][0],
        'linhas_em': tags[273],
        'x0': tags[33922][3],
        'y0': tags[33922][4],
        'passo_x': tags[33550][0],
        'passo_y': tags[33550][1],
        'nodata': float(tags.get(42113, str(NODATA)) or NODATA),
    }


def grade_de_saida(janela=JANELA, resolucao_m=RESOLUCAO_M):
    """Centros das células da grade do mapa: linhas uniformes em Web Mercator (de norte a sul),
    para a imagem casar com o L.imageOverlay. Devolve (latitudes, longitudes, limites)."""
    import numpy as np

    lat_media = (janela['sul'] + janela['norte']) / 2
    passo_lon = resolucao_m / (111_320 * math.cos(math.radians(lat_media)))
    colunas = round((janela['leste'] - janela['oeste']) / passo_lon)
    y_norte, y_sul = mercator_y(janela['norte']), mercator_y(janela['sul'])
    passo_y = math.radians(passo_lon)  # mesmo passo angular nos dois eixos de Mercator
    linhas = round((y_norte - y_sul) / passo_y)
    ys = y_norte - (np.arange(linhas) + 0.5) * (y_norte - y_sul) / linhas
    lons = janela['oeste'] + (np.arange(colunas) + 0.5) * (janela['leste'] - janela['oeste']) / colunas
    limites = [[janela['sul'], janela['oeste']], [janela['norte'], janela['leste']]]
    return latitude_de_mercator(ys), lons, limites


def janela_no_anadem(cabecalho, lats, lons, margem=3):
    """Linhas e colunas do ANADEM que cobrem a grade de saída: (l0, l1, c0, c1)."""
    import numpy as np

    lat, lon = np.meshgrid([lats.min(), lats.max()], [lons.min(), lons.max()])
    x, y = albers(*wgs84_para_sad69(lat.ravel(), lon.ravel()))
    c = (x - cabecalho['x0']) / cabecalho['passo_x']
    l = (cabecalho['y0'] - y) / cabecalho['passo_y']
    l0 = max(0, int(l.min()) - margem)
    l1 = min(cabecalho['altura'], int(l.max()) + margem + 1)
    c0 = max(0, int(c.min()) - margem)
    c1 = min(cabecalho['largura'], int(c.max()) + margem + 1)
    return l0, l1, c0, c1


def reamostrar(relevo, l0, c0, cabecalho, lats, lons):
    """Média 3×3 do ANADEM no centro de cada célula da grade de saída (metros; NaN sem dado)."""
    import numpy as np

    valido = ~np.isnan(relevo)
    soma = np.where(valido, relevo, 0.0)
    # Somas 3×3 por imagem integral.
    def caixa(a):
        integral = np.pad(a, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        return integral[3:, 3:] - integral[:-3, 3:] - integral[3:, :-3] + integral[:-3, :-3]
    somas, contagens = caixa(soma), caixa(valido.astype(float))
    lat, lon = np.meshgrid(lats, lons, indexing='ij')
    x, y = albers(*wgs84_para_sad69(lat, lon))
    c = np.round((x - cabecalho['x0']) / cabecalho['passo_x'] - 0.5).astype(int) - c0 - 1
    l = np.round((cabecalho['y0'] - y) / cabecalho['passo_y'] - 0.5).astype(int) - l0 - 1
    dentro = (l >= 0) & (c >= 0) & (l < somas.shape[0]) & (c < somas.shape[1])
    saida = np.full(lat.shape, np.nan)
    li, ci = l[dentro], c[dentro]
    with np.errstate(invalid='ignore', divide='ignore'):
        saida[dentro] = np.where(contagens[li, ci] > 0, somas[li, ci] / contagens[li, ci], np.nan)
    return saida


# ---- Nível atual do Guaíba -------------------------------------------------

def interpretar_telemetria(xml_texto):
    """XML da telemetria da ANA → lista de (datetime em Brasília, nível em metros), do mais novo ao mais velho."""
    raiz = ET.fromstring(xml_texto)
    leituras = []
    for registro in raiz.iter('DadosHidrometereologicos'):
        campos = {filho.tag: (filho.text or '').strip() for filho in registro}
        try:
            quando = datetime.strptime(campos['DataHora'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=HORARIO_DE_BRASILIA)
            nivel = float(campos['Nivel']) / 100
        except (KeyError, ValueError):
            continue
        leituras.append((quando, nivel))
    return sorted(leituras, reverse=True)


def leituras_da_ana(codigo, dias, agora=None):
    """Leituras de nível (datetime, metros) de uma estação da ANA nos últimos `dias`, da mais nova à mais velha."""
    agora = agora or datetime.now(HORARIO_DE_BRASILIA)
    params = {
        'codEstacao': codigo,
        'dataInicio': (agora - timedelta(days=dias)).strftime('%d/%m/%Y'),
        'dataFim': agora.strftime('%d/%m/%Y'),
    }
    for tentativa in range(TENTATIVAS_SEM_LEITURA):
        if tentativa:
            time.sleep(2)  # a ANA às vezes responde vazio e, segundos depois, com os dados
        leituras = interpretar_telemetria(baixar(URL_TELEMETRIA, params=params, timeout=60).text)
        if leituras:
            return leituras
    raise ErroDeColeta(f'a ANA não devolveu leituras da estação {codigo}')


def nivel_do_guaiba(agora=None):
    """Leituras das últimas 48 h no Cais Mauá: {'atual': {...}, 'serie': [...]}. Cache de 10 min."""
    em_cache = cache.get('enchente:nivel')
    if em_cache:
        return em_cache
    leituras = leituras_da_ana(ESTACAO_CAIS_MAUA, 2, agora)
    resultado = {
        'atual': {'quando': leituras[0][0].isoformat(), 'metros': leituras[0][1]},
        'serie': [{'quando': q.isoformat(), 'metros': n} for q, n in leituras],
    }
    cache.set('enchente:nivel', resultado, 10 * 60)
    return resultado


# ---- Belém: maré (ANA) e áreas de risco (SGB) ------------------------------
# Em Belém a cheia vem da maré alta somada à chuva forte: a maré represa os canais e a
# chuva não escoa. O relevo do ANADEM fica ~3 m alto demais nas baixadas (mediana de
# 4,8 m dentro dos setores de inundação do SGB, que alagam com marés de ~1,5 m), então
# lá não há simulação "de banheira": mostramos a maré ao vivo, a chuva e as áreas de risco.

ESTACAO_BELEM = '31645000'  # Captação d'água da Cosanpa, ANA; nível a cada 15 min
DIAS_DE_MARE = 30
# Setorização de riscos geológicos de Belém, revisita de 2021 (SGB/CPRM, RIGEO).
URL_SETORES_BELEM = (
    'https://rigeo.sgb.gov.br/server/api/core/bitstreams/29567946-4a3f-48c2-8f39-6f29ef815580/content'
)
ARQUIVO_SETORES_BELEM = PASTA / 'belem_setores_risco.geojson'
CENTRO_BELEM = (-1.4558, -48.4902)


def preamares(leituras, janela_h=3, separacao_h=4):
    """Marés altas: máximos locais numa janela de ±`janela_h` horas, separados por pelo menos
    `separacao_h` horas. `leituras` em ordem cronológica: [(datetime, metros), ...].
    Só conta picos com a janela inteira dentro dos dados: no fim da série, com a maré ainda
    subindo, a última leitura pareceria um pico; a maré alta só se confirma quando ela vaza."""
    picos = []
    janela = timedelta(hours=janela_h)
    inicio = fim = 0
    for quando, nivel in leituras:
        if quando - leituras[0][0] < janela or leituras[-1][0] - quando < janela:
            continue
        while leituras[inicio][0] < quando - janela:
            inicio += 1
        while fim < len(leituras) and leituras[fim][0] <= quando + janela:
            fim += 1
        if nivel < max(n for _, n in leituras[inicio:fim]):
            continue
        if picos and (quando - picos[-1][0]).total_seconds() < separacao_h * 3600:
            continue
        picos.append((quando, nivel))
    return picos


def mare_de_belem(agora=None):
    """Nível agora em relação à média de 30 dias, tendência, curva de 48 h e estatísticas das
    marés altas. Sem referência de nível do mar na régua, a média de 30 dias faz esse papel."""
    em_cache = cache.get('enchente:mare_belem')
    if em_cache:
        return em_cache
    leituras = sorted(leituras_da_ana(ESTACAO_BELEM, DIAS_DE_MARE, agora))
    media = sum(n for _, n in leituras) / len(leituras)
    altas = preamares(leituras)
    if len(altas) < 4:
        raise ErroDeColeta('poucas marés altas para comparar')
    acima = sorted(n - media for _, n in altas)
    p90 = acima[int(0.9 * (len(acima) - 1))]
    ultima, penultima = leituras[-1], leituras[-2]
    limite_48h = ultima[0] - timedelta(hours=48)
    resultado = {
        'media_30d': round(media, 3),
        'atual': {
            'quando': ultima[0].isoformat(),
            'relativo': round(ultima[1] - media, 2),
            'tendencia': 'enchendo' if ultima[1] > penultima[1] else 'vazando' if ultima[1] < penultima[1] else 'parada',
        },
        'serie_48h': [{'quando': q.isoformat(), 'relativo': round(n - media, 2)} for q, n in leituras if q >= limite_48h],
        'mares_altas': {
            'media': round(sum(acima) / len(acima), 2),
            'p90': round(p90, 2),
            'maxima': round(acima[-1], 2),
            'quantidade': len(acima),
            'recentes': [{'quando': q.isoformat(), 'relativo': round(n - media, 2)} for q, n in altas if q >= limite_48h],
        },
    }
    cache.set('enchente:mare_belem', resultado, 10 * 60)
    return resultado
