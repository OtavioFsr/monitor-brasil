from datetime import date, datetime, timezone

from django.test import SimpleTestCase

from dados.coletores import chuva, desmatamento, queimadas, sismos
from dados.coletores.geo import centro_aproximado, uf_do_ponto
from dados.coletores.http import ErroDeColeta
from dados.coletores.ufs import sigla_por_nome, titulo

CABECALHO_INPE = (
    'id,lat,lon,data_hora_gmt,satelite,municipio,estado,pais,municipio_id,estado_id,'
    'pais_id,numero_dias_sem_chuva,precipitacao,risco_fogo,bioma,frp\n'
)


def linha_inpe(satelite='NOAA-21', id_='a1', lat='  -3.422600', lon=' -54.157900',
               quando='2026-10-30 04:12:00', estado='PARÁ', frp='44.3'):
    return (f'{id_},{lat},{lon},{quando},{satelite},URUARÁ,{estado},Brasil,1508159,15,33,'
            f',,,Amazônia,{frp}\n')


def grib_merge(valores, lat_sul=-10.0, lon_oeste=300.0, passo=1.0, data=20261029, missing=9999):
    """GRIB2 com as duas mensagens do MERGE: nº de estações (ignorada) e a chuva.

    `valores`: lista de linhas, do sul para o norte.
    """
    import eccodes

    nj, ni = len(valores), len(valores[0])
    mensagens = b''
    for categoria, numero, plano in ((3, 1, [1.0] * (ni * nj)), (15, 5, [v for linha in valores for v in linha])):
        h = eccodes.codes_grib_new_from_samples('regular_ll_sfc_grib2')
        for chave, valor in [
            ('Ni', ni), ('Nj', nj), ('jScansPositively', 1),
            ('latitudeOfFirstGridPointInDegrees', lat_sul), ('longitudeOfFirstGridPointInDegrees', lon_oeste),
            ('latitudeOfLastGridPointInDegrees', lat_sul + (nj - 1) * passo),
            ('longitudeOfLastGridPointInDegrees', lon_oeste + (ni - 1) * passo),
            ('iDirectionIncrementInDegrees', passo), ('jDirectionIncrementInDegrees', passo),
            ('parameterCategory', categoria), ('parameterNumber', numero),
            ('dataDate', data), ('dataTime', 1200), ('bitmapPresent', 1), ('missingValue', missing),
        ]:
            eccodes.codes_set(h, chave, valor)
        eccodes.codes_set_values(h, [float(v) for v in plano])
        mensagens += eccodes.codes_get_message(h)
        eccodes.codes_release(h)
    return mensagens


class UfsTest(SimpleTestCase):
    def test_sigla_por_nome_ignora_acentos_e_caixa(self):
        self.assertEqual(sigla_por_nome('PARÁ'), 'PA')
        self.assertEqual(sigla_por_nome('sao paulo'), 'SP')
        self.assertEqual(sigla_por_nome('Distrito  Federal'), 'DF')
        self.assertEqual(sigla_por_nome('Bolívia'), '')

    def test_titulo(self):
        self.assertEqual(titulo('SÃO JOSÉ DOS CAMPOS'), 'São José dos Campos')


class GeoTest(SimpleTestCase):
    def test_uf_do_ponto_pela_malha_do_ibge(self):
        self.assertEqual(uf_do_ponto(-23.5632, -46.6544), 'SP')  # Av. Paulista
        self.assertEqual(uf_do_ponto(-15.7939, -47.8828), 'DF')  # Brasília
        self.assertEqual(uf_do_ponto(-3.1190, -60.0217), 'AM')   # Manaus
        self.assertEqual(uf_do_ponto(-34.6037, -58.3816), '')    # Buenos Aires
        self.assertEqual(uf_do_ponto(-20.0, -30.0), '')          # Atlântico

    def test_centro_aproximado(self):
        quadrado = {'type': 'Polygon', 'coordinates': [[[0, 0], [2, 0], [2, 2], [0, 2]]]}
        self.assertEqual(centro_aproximado(quadrado), (1.0, 1.0))
        self.assertIsNone(centro_aproximado({'type': 'Point', 'coordinates': [0, 0]}))


class QueimadasTest(SimpleTestCase):
    def test_interpreta_linha_do_inpe(self):
        [foco] = queimadas.interpretar(CABECALHO_INPE + linha_inpe())
        self.assertEqual(foco, {
            'id_inpe': 'a1',
            'data_hora': datetime(2026, 10, 30, 4, 12, tzinfo=timezone.utc),
            'latitude': -3.4226,
            'longitude': -54.1579,
            'satelite': 'NOAA-21',
            'municipio': 'Uruará',
            'uf': 'PA',
            'bioma': 'Amazônia',
            'frp': 44.3,
        })

    def test_descarta_geoestacionarios_e_linhas_ruins(self):
        csv = CABECALHO_INPE + ''.join([
            linha_inpe('GOES-19', 'g'),
            linha_inpe('MSG-03', 'm'),
            linha_inpe('AQUA_M-T', 'ok'),
            linha_inpe(id_='data-ruim', quando='ontem'),
            linha_inpe(id_='lat-ruim', lat='95'),
            linha_inpe(id_='sem-frp', frp=''),
        ])
        focos = queimadas.interpretar(csv)
        self.assertEqual([f['id_inpe'] for f in focos], ['ok', 'sem-frp'])
        self.assertIsNone(focos[1]['frp'])

    def test_pagina_de_erro_nao_e_csv(self):
        with self.assertRaises(ErroDeColeta):
            queimadas.interpretar('<html>404 Not Found</html>')

    def test_arquivos_de_ontem_e_hoje_em_utc(self):
        self.assertEqual(
            queimadas.datas_dos_arquivos(datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)),
            ['20251231', '20260101'],
        )
        self.assertEqual(
            queimadas.datas_dos_arquivos(datetime(2026, 3, 2, tzinfo=timezone.utc), dias=3),
            ['20260227', '20260228', '20260301', '20260302'],
        )


def feature_deter(gid='44436_curr', data='2026-10-23', uf='PA', **extra):
    return {
        'type': 'Feature',
        'geometry': {'type': 'MultiPolygon', 'coordinates': [[[[-58.04, -0.66], [-58.03, -0.66], [-58.03, -0.67], [-58.04, -0.67]]]]},
        'properties': {
            'gid': gid, 'classname': 'DESMATAMENTO_CR', 'view_date': data, 'sensor': 'AWFI',
            'satellite': 'CBERS-4', 'areamunkm': 0.0999, 'municipality': 'Oriximina', 'uf': uf,
            **extra,
        },
    }


class DesmatamentoTest(SimpleTestCase):
    def test_interpreta_feature_do_deter(self):
        [alerta] = desmatamento.interpretar({'features': [feature_deter()]}, 'Amazônia')
        self.assertEqual(alerta['id_externo'], 'Amazônia:44436_curr')
        self.assertEqual(alerta['data'], date(2026, 10, 23))
        self.assertEqual(alerta['uf'], 'PA')
        self.assertEqual(alerta['satelite'], 'CBERS-4 AWFI')
        self.assertAlmostEqual(alerta['latitude'], -0.665)
        self.assertAlmostEqual(alerta['longitude'], -58.035)
        self.assertEqual(alerta['geometria']['type'], 'MultiPolygon')

    def test_ignora_features_incompletas(self):
        geojson = {'features': [
            feature_deter(gid=None),
            feature_deter(data='sem data'),
            {**feature_deter(), 'geometry': None},
            feature_deter(gid='uf-estranha', uf='XX'),
        ]}
        [alerta] = desmatamento.interpretar(geojson, 'Cerrado')
        self.assertEqual(alerta['uf'], '')

    def test_resposta_sem_features_e_erro(self):
        with self.assertRaises(ErroDeColeta):
            desmatamento.interpretar({'erro': 'x'}, 'Amazônia')

    def test_nome_da_classe(self):
        self.assertEqual(desmatamento.nome_da_classe('DESMATAMENTO_CR'), 'Desmatamento (corte raso)')
        self.assertEqual(desmatamento.nome_da_classe('NOVA_CLASSE'), 'Nova classe')


CABECALHO_FDSN = ('#EventID|Time|Latitude|Longitude|Depth/km|Author|Catalog|Contributor|'
                  'ContributorID|MagType|Magnitude|MagAuthor|EventLocationName|EventType\n')


class SismosTest(SimpleTestCase):
    def test_so_eventos_em_territorio_brasileiro(self):
        texto = CABECALHO_FDSN + (
            'usp1|2026-10-20T10:00:00.5|-19.9|-43.9|5.04|x||USP|usp1|mR|2.34|x|Belo Horizonte/MG|earthquake\n'
            'usp2|2026-10-29T16:08:17|-22.02|-63.64|546.7|x||USP|usp2|mb|4.45|x|Salta Province, Argentina|earthquake\n'
            'usp3|data ruim|-19.9|-43.9|5|x||USP|usp3|mR|2|x|MG|earthquake\n'
        )
        [sismo] = sismos.interpretar(texto)
        self.assertEqual(sismo['id_evento'], 'usp1')
        self.assertEqual(sismo['uf'], 'MG')
        self.assertEqual(sismo['magnitude'], 2.3)
        self.assertEqual(sismo['profundidade_km'], 5.0)
        self.assertEqual(sismo['data_hora'].tzinfo, timezone.utc)

    def test_formato_inesperado_e_erro(self):
        with self.assertRaises(ErroDeColeta):
            sismos.interpretar('Error 400: Bad Request')


class ChuvaTest(SimpleTestCase):
    def test_interpreta_a_mensagem_de_chuva_e_recorta(self):
        import math

        # 4 × 5 células de 1°, de -10 a -7 de latitude e de -60 (300°) a -56 de longitude.
        valores = [
            [0, 1, 2, 3, 4],
            [5, 6, 9999, 8, 9],
            [10, 11, 12, 13, 14],
            [15, 16, 17, 18, 19],
        ]
        dia = chuva.interpretar(grib_merge(valores), limites=(-59.0, -9.0, -58.0, -8.0))
        self.assertEqual(dia['data'], date(2026, 10, 29))
        self.assertEqual((dia['lat_sul'], dia['lon_oeste'], dia['passo']), (-10.0, -60.0, 1.0))
        # Margem de 0,3°: uma célula a mais de cada lado do retângulo pedido.
        self.assertEqual(dia['grade'].shape, (4, 4))
        self.assertEqual(dia['grade'][0, 1], 1)
        self.assertTrue(math.isnan(dia['grade'][1, 2]))  # valor ausente vira NaN
        self.assertEqual(dia['grade'][3, 3], 18)

    def test_arquivo_ilegivel(self):
        with self.assertRaises(ErroDeColeta):
            chuva.interpretar(b'<html>erro</html>')

    def test_url_do_arquivo_diario(self):
        self.assertEqual(
            chuva.URL_DIARIO.format(data=date(2026, 3, 9)),
            'https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/2026/03/MERGE_CPTEC_20260309.grib2',
        )


class CidadesTest(SimpleTestCase):
    def test_dez_cidades_com_o_predio_do_bc_dentro_do_estado(self):
        from dados.cidades import CIDADES

        self.assertEqual(
            [c['nome'] for c in CIDADES],
            ['Belém', 'Fortaleza', 'Recife', 'Salvador', 'Brasília', 'Belo Horizonte',
             'Rio de Janeiro', 'São Paulo', 'Curitiba', 'Porto Alegre'],
        )
        self.assertEqual(len({c['id'] for c in CIDADES}), 10)
        self.assertEqual([c['nome'] for c in CIDADES if c['bc']['unidade'] == 'Sede'], ['Brasília'])
        for cidade in CIDADES:
            with self.subTest(cidade=cidade['nome']):
                self.assertEqual(uf_do_ponto(cidade['bc']['lat'], cidade['bc']['lon']), cidade['uf'])
                self.assertRegex(cidade['bc']['cep'], r'^\d{5}-\d{3}$')


class EnchenteTest(SimpleTestCase):
    def test_albers_na_origem_e_sentido_dos_eixos(self):
        from dados import enchente

        x, y = enchente.albers(-32.0, -60.0)
        self.assertAlmostEqual(float(x), 0, places=3)
        self.assertAlmostEqual(float(y), 0, places=3)
        x, y = enchente.albers(-30.0, -51.0)  # a leste e ao norte da origem
        self.assertGreater(float(x), 800_000)
        self.assertGreater(float(y), 0)

    def test_mercator_ida_e_volta_e_mudanca_de_datum_pequena(self):
        from dados import enchente

        self.assertAlmostEqual(float(enchente.latitude_de_mercator(enchente.mercator_y(-30.03))), -30.03, places=9)
        lat, lon = enchente.wgs84_para_sad69(-30.03, -51.23)
        # SAD69 e WGS84 diferem algumas dezenas de metros no RS.
        self.assertLess(abs(float(lat) + 30.03) * 111_000, 100)
        self.assertLess(abs(float(lon) + 51.23) * 96_000, 100)
        self.assertNotAlmostEqual(float(lat), -30.03, places=5)

    def test_cabecalho_do_tiff(self):
        import struct

        from dados import enchente

        tags = [  # (tag, tipo, quantidade, valor ou bytes externos)
            (256, 3, 1, 4), (257, 3, 1, 2), (258, 3, 1, 32), (259, 3, 1, 1), (273, 4, 2, None),
            (278, 3, 1, 1), (339, 3, 1, 3), (33550, 12, 3, None), (33922, 12, 6, None), (42113, 2, 7, None),
        ]
        externos = {
            273: struct.pack('<2I', 500, 516),
            33550: struct.pack('<3d', 30.0, 30.0, 0.0),
            33922: struct.pack('<6d', 0, 0, 0, 1000.0, 2000.0, 0),
            42113: b'-32768\x00',
        }
        ifd = 8
        dados = bytearray(b'II*\x00' + struct.pack('<I', ifd))
        dados += struct.pack('<H', len(tags))
        extra = ifd + 2 + 12 * len(tags) + 4
        blocos = b''
        for tag, tipo, quantidade, valor in tags:
            if valor is None:
                dados += struct.pack('<HHII', tag, tipo, quantidade, extra + len(blocos))
                blocos += externos[tag]
            else:
                dados += struct.pack('<HHI', tag, tipo, quantidade) + struct.pack('<HH', valor, 0)
        dados += b'\x00' * 4 + blocos
        cabecalho = enchente.ler_cabecalho_tiff(bytes(dados))
        self.assertEqual((cabecalho['largura'], cabecalho['altura'], cabecalho['linhas_em']), (4, 2, (500, 516)))
        self.assertEqual((cabecalho['x0'], cabecalho['y0'], cabecalho['passo_x'], cabecalho['nodata']), (1000.0, 2000.0, 30.0, -32768.0))
        with self.assertRaises(ErroDeColeta):
            enchente.ler_cabecalho_tiff(b'MM\x00*' + bytes(dados[4:]))

    def test_telemetria_da_ana(self):
        from dados import enchente

        xml = (
            '<DataTable><DocumentElement>'
            '<DadosHidrometereologicos><CodEstacao>87450004</CodEstacao><DataHora>2026-10-30 15:00:00</DataHora>'
            '<Vazao /><Nivel>130.00</Nivel><Chuva>0.00</Chuva></DadosHidrometereologicos>'
            '<DadosHidrometereologicos><CodEstacao>87450004</CodEstacao><DataHora>2026-10-30 16:00:00</DataHora>'
            '<Vazao /><Nivel>131.00</Nivel><Chuva>0.00</Chuva></DadosHidrometereologicos>'
            '<DadosHidrometereologicos><DataHora>2026-10-30 17:00:00</DataHora><Nivel></Nivel></DadosHidrometereologicos>'
            '</DocumentElement></DataTable>'
        )
        leituras = enchente.interpretar_telemetria(xml)
        self.assertEqual([round(n, 2) for _, n in leituras], [1.31, 1.30])
        self.assertEqual(leituras[0][0].isoformat(), '2026-10-30T16:00:00-03:00')

    def test_grade_de_saida_e_relevo_preparado(self):
        import json

        from dados import enchente

        lats, lons, limites = enchente.grade_de_saida()
        meta = json.loads(enchente.ARQUIVO_META.read_text(encoding='utf-8'))
        self.assertEqual((len(lats), len(lons)), (meta['linhas'], meta['colunas']))
        self.assertEqual(limites, meta['limites'])
        self.assertEqual(enchente.ARQUIVO_RELEVO.stat().st_size, meta['linhas'] * meta['colunas'] * 2)
        self.assertGreater(lats[0], lats[-1])  # de norte a sul

    def test_utm_22s_no_meridiano_central(self):
        from dados import enchente

        # 30° S no meridiano central da zona 22 (51° O): x = 500 000; y = 10 000 000 − k0 × arco de meridiano.
        lat, lon = enchente.utm_para_latlon(500_000, 10_000_000 - 0.9996 * 3_320_113.4)
        self.assertAlmostEqual(float(lat), -30.0, places=4)
        self.assertAlmostEqual(float(lon), -51.0, places=6)
        lat, lon = enchente.utm_para_latlon(478_000, 6_677_000)  # centro de Porto Alegre, aprox.
        self.assertAlmostEqual(float(lat), -30.04, delta=0.02)
        self.assertAlmostEqual(float(lon), -51.23, delta=0.02)

    def test_geometria_do_geopackage(self):
        import struct

        from dados import enchente

        anel = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
        wkb = struct.pack('<BII', 1, 3, 1) + struct.pack('<I', len(anel)) + b''.join(struct.pack('<dd', *p) for p in anel)
        multi = struct.pack('<BII', 1, 6, 2) + wkb + wkb
        blob = b'GP\x00\x01' + struct.pack('<i', 4326) + multi  # sem envelope
        self.assertEqual(enchente.geometria_gpkg(blob), [[anel], [anel]])
        linha = struct.pack('<BII', 1, 1002, 2) + struct.pack('<6d', 0, 0, 9, 1, 1, 9)  # LineString Z
        envelope = b'GP\x00\x03' + struct.pack('<i', 4326) + struct.pack('<4d', 0, 1, 0, 1)  # envelope xy
        self.assertEqual(enchente.geometria_gpkg(envelope + linha), [[[(0.0, 0.0), (1.0, 1.0)]]])

    def test_alcance_passa_pelo_ponto_mais_baixo_da_barreira(self):
        import numpy as np

        from dados import enchente

        # Lago (0) à esquerda, barreira no meio com uma brecha a 3 m, baixada (1 m) à direita.
        relevo = np.array([
            [0, 9, 1],
            [0, 3, 1],
            [0, 9, enchente.NODATA],
        ])
        alcance = enchente.calcular_alcance(relevo, [(0, 0)])
        self.assertEqual(alcance.tolist(), [[0, 9, 3], [0, 3, 3], [0, 9, enchente.SEM_ALCANCE]])

    def test_rasterizar_na_grade_do_mapa(self):
        from dados import enchente

        limites = [[-1.0, 0.0], [1.0, 4.0]]
        quadrado = [[[(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0), (0.0, 0.0)]]]  # metade oeste, metade norte
        mascara = enchente.rasterizar(quadrado, 4, 4, limites)
        self.assertEqual(mascara.astype(int).tolist(), [[1, 1, 0, 0], [1, 1, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]])

    def test_preamares_de_uma_mare_semidiurna(self):
        import math
        from datetime import datetime, timedelta

        from dados import enchente

        inicio = datetime(2026, 10, 1)
        # Maré de 12,42 h com amplitude que cresce; marés altas às 6 h, 18,4 h, 30,8 h e 43,3 h.
        # A série termina às 47,75 h subindo: essa ponta não pode virar maré alta.
        leituras = [(inicio + timedelta(minutes=15 * i),
                     6.4 + (1 + i / 400) * math.cos(2 * math.pi * (15 * i - 360) / (12.42 * 60))) for i in range(192)]
        altas = enchente.preamares(leituras)
        self.assertEqual(len(altas), 4)
        self.assertEqual(altas[0][0], inicio + timedelta(hours=6))
        self.assertTrue(all(b[1] > a[1] for a, b in zip(altas, altas[1:])))
        self.assertTrue(all(abs((b[0] - a[0]).total_seconds() / 3600 - 12.42) < 0.3 for a, b in zip(altas, altas[1:])))
