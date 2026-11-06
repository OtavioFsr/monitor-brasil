import json
import math
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timezone

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from dados import enchente
from dados.coletores.http import ErroDeColeta, baixar

URL_MUNICIPIOS_RS = 'https://servicodados.ibge.gov.br/api/v1/localidades/estados/RS/municipios'
URL_MALHA_MUNICIPIO = (
    'https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{codigo}'
    '?formato=application/vnd.geo+json&qualidade=intermediaria'
)
TAMANHO_CABECALHO = 256 * 1024


class Command(BaseCommand):
    help = ('Prepara o simulador de enchente do Guaíba: baixa do ANADEM (ANA/UFRGS) o relevo em volta de '
            'Porto Alegre e, do banco das cheias de 2024 do IPH/UFRGS, as manchas simuladas para Porto '
            'Alegre, a mancha observada em 06/05/2024 e os diques. Basta rodar uma vez (~4 min, ~220 MB).')

    def add_arguments(self, parser):
        parser.add_argument(
            '--gpkg', type=Path, metavar='ARQUIVO',
            help='Usar este cheias_rhguaiba_2024_db_v12.gpkg já baixado em vez de baixar do Zenodo (196 MB).',
        )

    def handle(self, *args, gpkg=None, **options):
        try:
            if gpkg:
                self._preparar(gpkg)
            else:
                with tempfile.TemporaryDirectory() as pasta:
                    destino = Path(pasta) / 'cheias.gpkg'
                    self.stdout.write('Baixando o banco das cheias de 2024 do IPH/UFRGS (196 MB)...')
                    self._baixar_arquivo(enchente.URL_CHEIAS_2024, destino)
                    self._preparar(destino)
        except ErroDeColeta as erro:
            raise CommandError(str(erro)) from erro

    def _baixar_arquivo(self, url, destino):
        cabecalhos = {'User-Agent': settings.COLETA_USER_AGENT}
        try:
            with requests.get(url, headers=cabecalhos, stream=True, timeout=120) as resposta:
                if resposta.status_code >= 400:
                    raise ErroDeColeta(f'HTTP {resposta.status_code} em {url}')
                with open(destino, 'wb') as arquivo:
                    for pedaco in resposta.iter_content(chunk_size=1024 * 1024):
                        arquivo.write(pedaco)
        except requests.RequestException as erro:
            raise ErroDeColeta(f'falha de rede ao baixar {url}: {erro.__class__.__name__}') from erro

    def _preparar(self, gpkg):
        import numpy as np

        self.stdout.write('Lendo o cabeçalho do ANADEM...')
        inicio = baixar(enchente.URL_ANADEM, cabecalhos={'Range': f'bytes=0-{TAMANHO_CABECALHO - 1}'})
        cabecalho = enchente.ler_cabecalho_tiff(inicio.content)

        lats, lons, limites = enchente.grade_de_saida()
        l0, l1, c0, c1 = enchente.janela_no_anadem(cabecalho, lats, lons)
        self.stdout.write(f'Baixando {l1 - l0} linhas × {c1 - c0} colunas do ANADEM '
                          f'(~{(l1 - l0) * (c1 - c0) * 4 / 2**20:.0f} MB, uma linha por pedido)...')
        relevo = self._baixar_janela(cabecalho, l0, l1, c0, c1)
        relevo[relevo == cabecalho['nodata']] = np.nan

        self.stdout.write(f'Reamostrando para {len(lats)} × {len(lons)} células de ~{enchente.RESOLUCAO_M} m...')
        altitudes = enchente.reamostrar(relevo, l0, c0, cabecalho, lats, lons)
        decimetros = np.where(np.isnan(altitudes), enchente.NODATA, np.round(altitudes * 10)).astype('<i2')

        self.stdout.write('Desenhando os municípios (malha do IBGE)...')
        municipios, nomes = self._municipios(lats, lons, limites)

        sementes = []
        for lat, lon in enchente.SEMENTES:
            linha = int(np.abs(lats - lat).argmin())
            coluna = int(np.abs(lons - lon).argmin())
            sementes.append([linha, coluna])
            if altitudes[linha, coluna] > 0.5:
                raise ErroDeColeta(f'a semente {lat}, {lon} caiu em terra ({altitudes[linha, coluna]:.1f} m)')

        self.stdout.write('Calculando até onde a água chega a cada nível (ANADEM)...')
        alcance = enchente.calcular_alcance(decimetros.astype(np.int32), sementes)

        self.stdout.write('Juntando as manchas simuladas do IPH/UFRGS para Porto Alegre...')
        conexao = sqlite3.connect(gpkg)
        iph = np.full(decimetros.shape, enchente.SEM_ALCANCE, dtype=np.int32)
        for camada, nivel in sorted(enchente.CAMADAS_IPH.items(), key=lambda item: item[1]):
            poligonos = [[self._utm_para_lonlat(anel) for anel in aneis]
                         for aneis in enchente.ler_camada_gpkg(conexao, camada)]
            mascara = enchente.rasterizar(poligonos, len(lats), len(lons), limites)
            iph[mascara & (iph == enchente.SEM_ALCANCE)] = round(nivel * 10)
        # Onde o IPH alaga antes, vale o IPH: no terreno (a célula fica abaixo daquele nível) e no alcance.
        decimetros = np.where(iph < decimetros, iph, decimetros).astype('<i2')
        alcance = np.minimum(alcance, iph)

        self.stdout.write('Desenhando a mancha observada em 06/05/2024 e os diques...')
        observado = enchente.rasterizar(
            enchente.ler_camada_gpkg(conexao, enchente.CAMADA_OBSERVADA), len(lats), len(lons), limites)
        diques = self._diques(conexao)
        conexao.close()

        enchente.PASTA.mkdir(parents=True, exist_ok=True)
        enchente.ARQUIVO_RELEVO.write_bytes(decimetros.tobytes())
        enchente.ARQUIVO_ALCANCE.write_bytes(np.minimum(alcance, enchente.SEM_ALCANCE).astype('<i2').tobytes())
        enchente.ARQUIVO_MUNICIPIOS.write_bytes(municipios.astype('u1').tobytes())
        enchente.ARQUIVO_OBSERVADO.write_bytes(observado.astype('u1').tobytes())
        enchente.ARQUIVO_DIQUES.write_text(json.dumps(diques, ensure_ascii=False), encoding='utf-8')
        lat_media = math.radians((limites[0][0] + limites[1][0]) / 2)
        lado_km = (limites[1][1] - limites[0][1]) / len(lons) * 111.32 * math.cos(lat_media)
        enchente.ARQUIVO_META.write_text(json.dumps({
            'limites': limites,
            'linhas': len(lats),
            'colunas': len(lons),
            'area_celula_km2': round(lado_km ** 2, 6),
            'nodata': enchente.NODATA,
            'sementes': sementes,
            'municipios': nomes,
            'niveis_iph': sorted(enchente.CAMADAS_IPH.values()),
            'fonte': ('ANADEM v1 (ANA e IPH/UFRGS), recorte das bacias do Guaíba, SNIRH; manchas e observação '
                      'de 2024: banco das cheias da RH do Guaíba, IPH/UFRGS (Zenodo, v1.2, CC-BY 4.0)'),
            'gerado_em': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        }, ensure_ascii=False, indent=1), encoding='utf-8')
        validos = decimetros[decimetros != enchente.NODATA] / 10
        self.stdout.write(self.style.SUCCESS(
            f'Pronto: {enchente.ARQUIVO_RELEVO.name} ({enchente.ARQUIVO_RELEVO.stat().st_size // 1024} KB), '
            f'altitudes de {validos.min():.1f} a {validos.max():.1f} m.'
        ))

    def _baixar_janela(self, cabecalho, l0, l1, c0, c1):
        """Uma linha por pedido (o servidor do SNIRH não aceita vários trechos num pedido só)."""
        import numpy as np

        sessao = requests.Session()
        sessao.headers['User-Agent'] = settings.COLETA_USER_AGENT
        largura_linha = cabecalho['largura'] * 4

        def linha(n):
            # As linhas são contíguas, cada uma com largura × 4 bytes a partir da primeira.
            inicio = cabecalho['linhas_em'][0] + n * largura_linha + c0 * 4
            fim = inicio + (c1 - c0) * 4 - 1
            for tentativa in range(3):
                try:
                    resposta = sessao.get(enchente.URL_ANADEM, headers={'Range': f'bytes={inicio}-{fim}'}, timeout=60)
                    if resposta.status_code == 206 and len(resposta.content) == fim - inicio + 1:
                        return np.frombuffer(resposta.content, dtype='<f4')
                except requests.RequestException:
                    pass
            raise ErroDeColeta(f'não consegui baixar a linha {n} do ANADEM')

        # O endereço da linha é calculado; confere com a tabela de strips do TIFF.
        if cabecalho['linhas_em'][1] - cabecalho['linhas_em'][0] != largura_linha:
            raise ErroDeColeta('as linhas do ANADEM não são contíguas')
        with ThreadPoolExecutor(max_workers=6) as executor:
            linhas = list(executor.map(linha, range(l0, l1)))
        return np.vstack(linhas).astype(float)

    @staticmethod
    def _utm_para_lonlat(anel):
        lat, lon = enchente.utm_para_latlon([p[0] for p in anel], [p[1] for p in anel])
        return list(zip(lon.tolist(), lat.tolist()))

    def _diques(self, conexao):
        """Diques e muro da Mauá como GeoJSON (linhas em lon/lat), com o nome quando houver."""
        features = []
        for blob, nome in conexao.execute(f'select geom, coalesce(NM_DIQUE, nm) from "{enchente.CAMADA_DIQUES}"'):
            for (linha,) in enchente.geometria_gpkg(blob):
                features.append({
                    'type': 'Feature',
                    'geometry': {'type': 'LineString', 'coordinates': [[round(x, 6), round(y, 6)] for x, y in linha]},
                    'properties': {'nome': self._nome_do_dique(nome)},
                })
        return {'type': 'FeatureCollection', 'features': features}

    @staticmethod
    def _nome_do_dique(codigo):
        if codigo and codigo.startswith('dique_'):
            return 'Dique ' + codigo[len('dique_'):].replace('_', ' ').title().replace('Polder', 'do Pôlder')
        return f'Dique ou muro de proteção (trecho {codigo})' if codigo else 'Dique ou muro de proteção'

    def _municipios(self, lats, lons, limites):
        import numpy as np

        codigos = {m['nome']: m['id'] for m in baixar(URL_MUNICIPIOS_RS).json()}
        mascara = np.zeros((len(lats), len(lons)), dtype=np.uint8)
        nomes = []
        for nome in enchente.MUNICIPIOS:
            codigo = codigos.get(nome)
            if not codigo:
                raise ErroDeColeta(f'o IBGE não listou o município {nome}')
            geometria = baixar(URL_MALHA_MUNICIPIO.format(codigo=codigo)).json()['features'][0]['geometry']
            poligonos = [geometria['coordinates']] if geometria['type'] == 'Polygon' else geometria['coordinates']
            nomes.append({'nome': nome, 'codigo': str(codigo)})
            mascara[enchente.rasterizar(poligonos, len(lats), len(lons), limites) & (mascara == 0)] = len(nomes)
        return mascara, nomes
