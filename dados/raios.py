"""Raios detectados pelo GLM do satélite GOES-19, publicados pelo INPE/CPTEC.

O GLM (Geostationary Lightning Mapper) enxerga os clarões dos raios de dia e de noite.
O INPE publica a cada 5 min uma imagem JPG da América do Sul (fundo preto, 0,02° ≈ 2 km
por pixel, georreferenciada pelo .jgw ao lado) com a densidade de "grupos" ópticos em
cada pixel: cada descarga gera vários grupos. A cor segue a barra desenhada no rodapé
da própria imagem (0 a 500), que usamos para voltar ao número pelo brilho do pixel.

Cada quadro vira uma lista de células de 0,1° (~11 km) dentro do Brasil com a maior
densidade de cada uma, e fica em cache: a camada junta os quadros dos últimos minutos e
mostra, por célula, a densidade máxima e há quanto tempo foi o raio mais recente.
O INPE guarda cerca de 8 dias de imagens; nada vai para o banco.
"""

import io
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from django.core.cache import cache

from .coletores.geo import SIGLAS_DA_MASCARA, limites_do_brasil, mascara_ufs
from .coletores.http import ErroDeColeta, baixar

URL = 'https://ftp.cptec.inpe.br/goes/goes19/broadcast/glm/'
PADRAO_ARQUIVO = re.compile(r'GLM_(\d{12})\.jpg')
INTERVALO_MIN = 5
CELULA_GRAUS = 0.1
MARGEM_GRAUS = 0.5
# Pixel escuro é fundo (o JPG deixa um chuvisco de tons quase pretos em volta dos pontos).
CANAL_MINIMO = 60
AMOSTRAS_DA_BARRA = 101  # pontos lidos na barra de 0 a 500
JANELAS_MIN = (15, 30, 60)
CACHE_LISTAGEM = 120
CACHE_QUADRO = 3 * 60 * 60
QUADROS_SIMULTANEOS = 4


def interpretar_listagem(html):
    """Horários (AAAAMMDDhhmm, UTC) das imagens na pasta, em ordem."""
    return sorted(set(PADRAO_ARQUIVO.findall(html)))


def interpretar_jgw(texto):
    """World file → (passo em graus, lon do centro do 1º pixel, lat do centro do 1º pixel)."""
    a, _, _, e, c, f = (float(linha) for linha in texto.split())
    if abs(a + e) > 1e-9:
        raise ErroDeColeta(f'georreferência inesperada no .jgw do GLM: {a}, {e}')
    return a, c, f


def _barra_de_cores(pixels):
    """(cores RGB, valores) da barra no rodapé: a faixa larga de pixels não pretos mais baixa."""
    import numpy as np

    altura, largura = pixels.shape[:2]
    acesas = pixels.max(axis=2) > CANAL_MINIMO
    linhas = [y for y in range(altura - 1, int(altura * 0.9), -1) if acesas[y].sum() > largura * 0.15]
    if not linhas:
        raise ErroDeColeta('a imagem do GLM não tem a barra de cores no rodapé')
    meio = linhas[len(linhas) // 2]
    colunas = np.nonzero(acesas[meio])[0]
    x0, x1 = colunas[0] + 2, colunas[-1] - 2  # sem a borda da barra
    amostras = np.linspace(x0, x1, AMOSTRAS_DA_BARRA).round().astype(int)
    # Brilho de cada amostra, forçado a crescer (o JPEG deixa pequenas oscilações).
    brilho = np.maximum.accumulate(_brilho(pixels[meio, amostras])) + np.arange(AMOSTRAS_DA_BARRA) * 1e-6
    return brilho, np.linspace(0, 500, AMOSTRAS_DA_BARRA), min(linhas)


def _brilho(rgb):
    """Luminância (o Y do JPEG), que o JPEG guarda em resolução cheia: a cor (crominância) vai
    pela metade e, num ponto de poucos pixels, se mistura com o preto em volta."""
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def interpretar(conteudo, geometria, limites=None):
    """JPG do GLM → [(lat, lon, densidade, índice da UF)] das células de 0,1° com raio no Brasil."""
    import numpy as np
    from PIL import Image

    passo, lon0, lat0 = geometria
    pixels = np.asarray(Image.open(io.BytesIO(conteudo)).convert('RGB'))
    brilho_da_barra, valores, topo_da_barra = _barra_de_cores(pixels)

    lon_min, lat_min, lon_max, lat_max = limites or limites_do_brasil()
    lado = max(1, round(CELULA_GRAUS / passo))  # pixels por célula
    # Recorte alinhado às células: começa num múltiplo de `lado` a partir do canto da imagem.
    c0 = max(0, int((lon_min - MARGEM_GRAUS - lon0) / passo) // lado * lado)
    c1 = min(pixels.shape[1], int((lon_max + MARGEM_GRAUS - lon0) / passo) + 1)
    r0 = max(0, int((lat0 - lat_max - MARGEM_GRAUS) / passo) // lado * lado)
    r1 = min(topo_da_barra - 1, int((lat0 - lat_min + MARGEM_GRAUS) / passo) + 1)
    recorte = pixels[r0:r1, c0:c1]
    linhas_acesas, colunas_acesas = np.nonzero(recorte.max(axis=2) > CANAL_MINIMO)
    if not len(linhas_acesas):
        return []

    # Brilho de cada pixel aceso → valor do mesmo brilho na barra. Acima de ~125 a barra vai do
    # amarelo ao branco quase sem mudar de brilho: ali o valor é aproximado.
    brilho = _brilho(recorte[linhas_acesas, colunas_acesas].astype(float))
    densidade = np.interp(brilho, brilho_da_barra, valores)

    n_linhas, n_colunas = -(-recorte.shape[0] // lado), -(-recorte.shape[1] // lado)
    maximo = np.zeros((n_linhas, n_colunas))
    np.maximum.at(maximo, (linhas_acesas // lado, colunas_acesas // lado), densidade)

    # Máscara das UFs na mesma grade (que ela quer do sul para o norte).
    passo_celula = lado * passo
    lat_norte = lat0 - (r0 + (lado - 1) / 2) * passo
    lon_oeste = lon0 + (c0 + (lado - 1) / 2) * passo
    lat_sul = lat_norte - (n_linhas - 1) * passo_celula
    mascara = mascara_ufs(round(lat_sul, 4), round(lon_oeste, 4), round(passo_celula, 6), n_linhas, n_colunas)[::-1]

    celulas = []
    for i, j in zip(*np.nonzero((maximo >= 1) & (mascara > 0))):
        celulas.append((
            round(float(lat_norte - i * passo_celula), 3), round(float(lon_oeste + j * passo_celula), 3),
            int(round(maximo[i, j])), int(mascara[i, j]),
        ))
    return celulas


def como_datetime(horario):
    return datetime.strptime(horario, '%Y%m%d%H%M').replace(tzinfo=timezone.utc)


def _listagem():
    em_cache = cache.get('raios:listagem')
    if em_cache:
        return em_cache
    horarios = interpretar_listagem(baixar(URL, timeout=30).text)
    if not horarios:
        raise ErroDeColeta('a pasta do GLM no INPE veio vazia')
    geometria = interpretar_jgw(baixar(f'{URL}GLM_{horarios[-1]}.jgw', timeout=30).text)
    resultado = {'horarios': horarios, 'geometria': geometria}
    cache.set('raios:listagem', resultado, CACHE_LISTAGEM)
    return resultado


def _quadro(horario, geometria):
    chave = f'raios:quadro:{horario}'
    celulas = cache.get(chave)
    if celulas is None:
        celulas = interpretar(baixar(f'{URL}GLM_{horario}.jpg', tentativas=2, timeout=60).content, geometria)
        cache.set(chave, celulas, CACHE_QUADRO)
    return celulas


def recentes(minutos=30, uf=''):
    """Raios dos últimos `minutos` antes da imagem mais recente. Levanta ErroDeColeta.

    {'ultimo', 'minutos', 'quadros', 'celulas': [[lat, lon, densidade máxima, minutos desde o
    raio mais recente da célula até a última imagem], ...]} com as mais antigas primeiro.
    """
    listagem = _listagem()
    ultimo = como_datetime(listagem['horarios'][-1])
    desde = ultimo - timedelta(minutes=minutos) + timedelta(seconds=1)
    janela = [h for h in listagem['horarios'] if como_datetime(h) >= desde]
    with ThreadPoolExecutor(QUADROS_SIMULTANEOS) as grupo:  # na primeira vez, baixa e lê em paralelo
        quadros = list(grupo.map(lambda horario: _quadro(horario, listagem['geometria']), janela))
    indice_uf = SIGLAS_DA_MASCARA.index(uf) if uf else None
    por_celula = {}
    for horario, quadro in zip(janela, quadros):  # do mais antigo ao mais novo: o mais novo define a idade
        idade = round((ultimo - como_datetime(horario)) / timedelta(minutes=1))
        for lat, lon, densidade, indice in quadro:
            if indice_uf is not None and indice != indice_uf:
                continue
            anterior = por_celula.get((lat, lon))
            por_celula[(lat, lon)] = [lat, lon, max(densidade, anterior[2] if anterior else 0), idade]
    celulas = sorted(por_celula.values(), key=lambda c: -c[3])
    return {'ultimo': ultimo.isoformat(), 'minutos': minutos, 'quadros': len(janela), 'celulas': celulas}
