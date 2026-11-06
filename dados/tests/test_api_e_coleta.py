from datetime import date, timedelta
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from dados.coletores.http import ErroDeColeta
from dados.models import AlertaDesmatamento, ChuvaDiaria, Coleta, FocoQueimada, Sismo
from dados.tests.test_coletores import CABECALHO_INPE, feature_deter, grib_merge, linha_inpe

QUADRADO = {'type': 'Polygon', 'coordinates': [[[-58.04, -0.66], [-58.03, -0.66], [-58.03, -0.67], [-58.04, -0.66]]]}


def foco(id_, uf, horas_atras, frp=10.0):
    return FocoQueimada.objects.create(
        id_inpe=id_, data_hora=timezone.now() - timedelta(hours=horas_atras),
        latitude=-3.4, longitude=-54.1, satelite='NOAA-21', municipio='Uruará', uf=uf,
        bioma='Amazônia', frp=frp,
    )


class ApiTest(TestCase):
    def setUp(self):
        foco('recente-pa', 'PA', 2)
        foco('recente-mt', 'MT', 5)
        foco('antigo-pa', 'PA', 24 * 5)
        AlertaDesmatamento.objects.create(
            id_externo='Amazônia:1', bioma='Amazônia', classe='DESMATAMENTO_CR',
            data=timezone.localdate() - timedelta(days=3), area_km2=0.5, municipio='Oriximina',
            uf='PA', latitude=-0.66, longitude=-58.03, geometria=QUADRADO,
        )
        Sismo.objects.create(
            id_evento='usp1', data_hora=timezone.now() - timedelta(days=10), latitude=-19.9,
            longitude=-43.9, magnitude=2.3, local='Belo Horizonte/MG', uf='MG',
        )

    def test_pagina_do_mapa(self):
        resposta = self.client.get(reverse('dados:mapa'))
        self.assertContains(resposta, 'Monitor Brasil')
        self.assertContains(resposta, 'São Paulo (SP)')

    def test_pagina_tem_cidades_em_foco_e_secoes_minimizaveis(self):
        resposta = self.client.get(reverse('dados:mapa'))
        self.assertContains(resposta, '<option value="porto-alegre">Porto Alegre (RS)</option>', html=True)
        self.assertContains(resposta, 'id="cidades" type="application/json"')
        self.assertContains(resposta, 'banco_central.svg')
        self.assertContains(resposta, '<details class="secao box box-saude" id="secao-saude">')
        self.assertContains(resposta, '<details class="secao" id="secao-coleta">')
        self.assertNotContains(resposta, 'id="secao-estados"')

    def test_focos_filtra_periodo_e_uf(self):
        geojson = self.client.get(reverse('dados:focos')).json()
        self.assertEqual(len(geojson['features']), 2)
        self.assertFalse(geojson['truncado'])
        geojson = self.client.get(reverse('dados:focos'), {'dias': 7, 'uf': 'pa'}).json()
        self.assertEqual(len(geojson['features']), 2)
        self.assertEqual({f['properties']['uf'] for f in geojson['features']}, {'PA'})
        ponto = geojson['features'][0]['geometry']
        self.assertEqual(ponto, {'type': 'Point', 'coordinates': [-54.1, -3.4]})

    def test_mapa_de_calor_soma_focos_por_celula(self):
        FocoQueimada.objects.create(
            id_inpe='vizinho', data_hora=timezone.now(), latitude=-3.43, longitude=-54.14,
            satelite='NOAA-20', uf='PA',
        )
        calor = self.client.get(reverse('dados:focos_calor')).json()
        # -3.4/-54.1 (dois focos) e -3.43/-54.14 caem na mesma célula de 0,1°.
        self.assertEqual(calor['celulas'], [[-3.4, -54.1, 3]])
        self.assertEqual((calor['focos'], calor['maximo']), (3, 3))
        calor = self.client.get(reverse('dados:focos_calor'), {'dias': 7, 'uf': 'MT'}).json()
        self.assertEqual(calor['celulas'], [[-3.4, -54.1, 1]])
        self.assertEqual(self.client.get(reverse('dados:focos_calor'), {'uf': 'XX'}).status_code, 400)

    def test_parametros_invalidos_dao_400(self):
        for params in ({'dias': 'abc'}, {'dias': 0}, {'dias': 999}, {'uf': 'XX'}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(reverse('dados:focos'), params).status_code, 400)
        resposta = self.client.get(reverse('dados:resumo'), {'dias_sismos': 'x'})
        self.assertEqual(resposta.status_code, 400)

    def test_desmatamento_devolve_poligonos_com_classe_legivel(self):
        [feature] = self.client.get(reverse('dados:desmatamento')).json()['features']
        self.assertEqual(feature['geometry'], QUADRADO)
        self.assertEqual(feature['properties']['classe'], 'Desmatamento (corte raso)')

    def test_sismos(self):
        [feature] = self.client.get(reverse('dados:sismos')).json()['features']
        self.assertEqual(feature['properties']['uf'], 'MG')
        self.assertEqual(self.client.get(reverse('dados:sismos'), {'dias': 5}).json()['features'], [])

    def test_resumo_por_uf(self):
        resumo = self.client.get(reverse('dados:resumo')).json()
        por_uf = {linha['uf']: linha for linha in resumo['ufs']}
        self.assertEqual(por_uf['PA']['focos'], 1)
        self.assertEqual(por_uf['PA']['alertas'], 1)
        self.assertEqual(por_uf['PA']['area_km2'], 0.5)
        self.assertEqual(por_uf['MG']['sismos'], 1)
        self.assertEqual(por_uf['PA']['nome'], 'Pará')
        self.assertIsNone(resumo['coletas']['queimadas']['ultimo_sucesso'])

    def test_resumo_distingue_coleta_em_andamento_de_falha(self):
        Coleta.objects.create(fonte='sismos', iniciada_em=timezone.now())
        Coleta.objects.create(
            fonte='queimadas', iniciada_em=timezone.now(), concluida_em=timezone.now(), erro='HTTP 503',
        )
        coletas = self.client.get(reverse('dados:resumo')).json()['coletas']
        self.assertTrue(coletas['sismos']['em_andamento'])
        self.assertIsNone(coletas['sismos']['sucesso'])
        self.assertFalse(coletas['queimadas']['em_andamento'])
        self.assertFalse(coletas['queimadas']['sucesso'])


def grade_brasil(mm, nan_em=None):
    """Grade de 0,25° cobrindo o Brasil com `mm` em todas as células (NaN em `nan_em`=(i, j))."""
    import numpy as np

    grade = np.full((160, 170), mm, dtype='f4')  # de -34° a 5,75° e de -74° a -31,75°
    if nan_em:
        grade[nan_em] = np.nan
    return {'lat_sul': -34.0, 'lon_oeste': -74.0, 'passo': 0.25, 'linhas': 160, 'colunas': 170, 'grade': grade}


class ChuvaApiTest(TestCase):
    # Brasília cai na célula i=73 (-15,75°), j=104 (-48,0°).
    BRASILIA = {'lat': -15.79, 'lon': -47.88}

    def setUp(self):
        from dados.coletores.chuva import gravar

        gravar({'data': date(2026, 10, 28), **grade_brasil(2.0)})
        gravar({'data': date(2026, 10, 29), **grade_brasil(3.0, nan_em=(73, 104))})

    def test_um_dia_e_o_mais_recente_gravado(self):
        resposta = self.client.get(reverse('dados:chuva')).json()
        self.assertEqual((resposta['inicio'], resposta['fim'], resposta['dias']), ('2026-10-29', '2026-10-29', 1))
        self.assertEqual(resposta['media_mm'], 3.0)
        self.assertEqual(resposta['limites'], [[-34.125, -74.125], [5.875, -31.625]])
        self.assertIn('fim=2026-10-29', resposta['imagem'])

    def test_varios_dias_somam_e_nan_de_um_dia_nao_apaga_o_outro(self):
        resposta = self.client.get(reverse('dados:chuva'), {'dias': 7}).json()
        self.assertEqual((resposta['inicio'], resposta['dias']), ('2026-10-28', 2))
        self.assertEqual(resposta['maximo_mm'], 5.0)
        ponto = self.client.get(reverse('dados:chuva_no_ponto'), {**self.BRASILIA, 'dias': 7}).json()
        self.assertEqual(ponto['mm'], 2.0)  # dia 29 sem dado em Brasília
        self.assertEqual(ponto['uf'], 'DF')
        um_dia = self.client.get(reverse('dados:chuva_no_ponto'), self.BRASILIA).json()
        self.assertIsNone(um_dia['mm'])

    def test_ponto_fora_do_brasil_e_parametros_invalidos(self):
        oceano = self.client.get(reverse('dados:chuva_no_ponto'), {'lat': -20, 'lon': -35}).json()
        self.assertEqual((oceano['mm'], oceano['uf']), (None, ''))
        self.assertEqual(self.client.get(reverse('dados:chuva_no_ponto'), {'lat': 'x'}).status_code, 400)
        self.assertEqual(self.client.get(reverse('dados:chuva'), {'uf': 'XX'}).status_code, 400)

    def test_imagem_png_recortada_no_brasil_e_na_uf(self):
        from io import BytesIO

        from PIL import Image

        brasil = Image.open(BytesIO(self.client.get(reverse('dados:chuva_png')).content))
        df = Image.open(BytesIO(self.client.get(reverse('dados:chuva_png'), {'uf': 'DF'}).content))
        self.assertEqual(brasil.format, 'PNG')
        self.assertEqual(brasil.width, 170)
        self.assertGreater(brasil.height, 160)  # Web Mercator estica as latitudes altas
        pintados_brasil = sum(1 for p in brasil.getdata() if p)  # 0 = transparente
        pintados_df = sum(1 for p in df.getdata() if p)
        self.assertGreater(pintados_df, 0)
        self.assertGreater(pintados_brasil, 100 * pintados_df)

    def test_resumo_traz_a_chuva_media_por_uf(self):
        por_uf = {linha['uf']: linha for linha in self.client.get(reverse('dados:resumo')).json()['ufs']}
        self.assertEqual(por_uf['SP']['chuva_mm'], 3.0)
        self.assertEqual(len(por_uf), 27)
        [so_df] = self.client.get(reverse('dados:resumo'), {'uf': 'DF', 'dias_chuva': 2}).json()['ufs']
        self.assertEqual(so_df['uf'], 'DF')
        # 5 mm em todo o DF, menos a célula de Brasília, que só tem os 2 mm do dia 28.
        self.assertTrue(2.0 < so_df['chuva_mm'] < 5.0)

    def test_sem_dados(self):
        ChuvaDiaria.objects.all().delete()
        self.assertEqual(self.client.get(reverse('dados:chuva')).json(), {'dias': 0})
        self.assertEqual(self.client.get(reverse('dados:chuva_png')).status_code, 404)


class RespostaFalsa:
    def __init__(self, texto='', dados=None, conteudo=b''):
        self.text = texto
        self.content = conteudo
        self._dados = dados
        self.encoding = None

    def json(self):
        return self._dados


class ComandoColetarTest(TestCase):
    def test_coleta_grava_e_atualiza_sem_duplicar(self):
        csv = CABECALHO_INPE + linha_inpe(id_='x1') + linha_inpe('NOAA-20', 'x2')
        with mock.patch('dados.coletores.queimadas.baixar', return_value=RespostaFalsa(csv)):
            call_command('coletar', fonte=['queimadas'], stdout=mock.MagicMock())
            call_command('coletar', fonte=['queimadas'], stdout=mock.MagicMock())
        # Dois arquivos (ontem e hoje) com os mesmos ids, duas coletas: continuam 2 focos.
        self.assertEqual(FocoQueimada.objects.count(), 2)
        self.assertEqual(Coleta.objects.filter(fonte='queimadas', sucesso=True).count(), 2)

    def test_dias_anteriores_de_queimadas_com_arquivo_ausente(self):
        urls = []

        def baixar(url, **kwargs):
            urls.append(url)
            if len(urls) == 1:  # o dia mais antigo já saiu do servidor
                raise ErroDeColeta('HTTP 404')
            return RespostaFalsa(CABECALHO_INPE + linha_inpe(id_=url[-12:-4]))

        with mock.patch('dados.coletores.queimadas.baixar', side_effect=baixar):
            call_command('coletar', fonte=['queimadas'], dias_queimadas=3, stdout=mock.MagicMock())
        self.assertEqual(len(urls), 4)  # 3 dias anteriores + hoje
        self.assertEqual(FocoQueimada.objects.count(), 3)
        coleta = Coleta.objects.get()
        self.assertTrue(coleta.sucesso)
        self.assertEqual(coleta.registros, 3)
        self.assertIn(f'{urls[0][-12:-4]}: HTTP 404', coleta.erro)

    def test_chuva_baixa_so_os_dias_que_faltam(self):
        urls = []

        def baixar(url, **kwargs):
            urls.append(url)
            if len(urls) == 4:  # o arquivo de hoje ainda não saiu: falha esperada
                raise ErroDeColeta('HTTP 404')
            dia = int(url[-14:-6])
            return RespostaFalsa(conteudo=grib_merge([[1.0] * 3] * 3, lat_sul=-16.0, lon_oeste=-49.0, data=dia))

        with mock.patch('dados.coletores.chuva.baixar', side_effect=baixar):
            call_command('coletar', fonte=['chuva'], stdout=mock.MagicMock())
            self.assertEqual(ChuvaDiaria.objects.count(), 3)
            call_command('coletar', fonte=['chuva'], stdout=mock.MagicMock())
        # Segunda rodada: os 3 dias anteriores já estão gravados; só tenta hoje de novo.
        self.assertEqual(len(urls), 5)
        self.assertEqual(urls[3], urls[4])
        primeira, segunda = Coleta.objects.filter(fonte='chuva').order_by('iniciada_em')
        self.assertEqual((primeira.sucesso, primeira.registros, primeira.erro), (True, 3, ''))
        self.assertEqual((segunda.sucesso, segunda.registros), (True, 1))

    def test_dias_queimadas_fora_do_intervalo(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command('coletar', fonte=['queimadas'], dias_queimadas=0, stdout=mock.MagicMock())

    def test_fonte_fora_do_ar_fica_registrada_e_nao_derruba_as_outras(self):
        texto_fdsn = (
            '#EventID|Time|Latitude|Longitude|Depth/km|Author|Catalog|Contributor|'
            'ContributorID|MagType|Magnitude|MagAuthor|EventLocationName|EventType\n'
            'usp1|2026-10-20T10:00:00|-19.9|-43.9|5|x||USP|usp1|mR|2.3|x|MG|earthquake\n'
        )
        with mock.patch('dados.coletores.queimadas.baixar', side_effect=ErroDeColeta('HTTP 503')), \
             mock.patch('dados.coletores.sismos.baixar', return_value=RespostaFalsa(texto_fdsn)):
            call_command('coletar', fonte=['queimadas', 'sismos'], stdout=mock.MagicMock())
        falha = Coleta.objects.get(fonte='queimadas')
        self.assertFalse(falha.sucesso)
        self.assertIn('HTTP 503', falha.erro)
        self.assertTrue(Coleta.objects.get(fonte='sismos').sucesso)
        self.assertEqual(Sismo.objects.count(), 1)

    def test_desmatamento_pagina_ate_a_data_limite(self):
        # Páginas de 2 alertas, do mais recente para o mais antigo.
        datas = ['2026-10-23', '2026-10-20', '2026-10-01', '2026-09-20', '2026-09-10', '2026-09-01']
        pedidos = []

        def baixar(url, params=None, **kwargs):
            if 'cerrado' in url:
                return RespostaFalsa(dados={'features': []})
            inicio = params['startIndex']
            pedidos.append(inicio)
            return RespostaFalsa(dados={'features': [
                feature_deter(gid=f'g{i}', data=datas[i]) for i in range(inicio, min(inicio + 2, len(datas)))
            ]})

        hoje = date(2026, 10, 30)
        with mock.patch('dados.coletores.desmatamento.ALERTAS_POR_PAGINA', 2), \
             mock.patch('dados.coletores.desmatamento.baixar', side_effect=baixar), \
             mock.patch('django.utils.timezone.localdate', return_value=hoje):
            call_command('coletar', fonte=['desmatamento'], stdout=mock.MagicMock())
        # Limite inicial: 30/09. A 2ª página (01/10 e 20/09) já passa dele; para ali.
        self.assertEqual(pedidos, [0, 2])
        self.assertEqual(AlertaDesmatamento.objects.count(), 4)

    def test_desmatamento_aproveita_paginas_antes_de_uma_falha(self):
        def baixar(url, params=None, **kwargs):
            if 'cerrado' in url or params['startIndex'] > 0:
                raise ErroDeColeta('HTTP 502')
            return RespostaFalsa(dados={'features': [feature_deter(gid='a'), feature_deter(gid='b')]})

        with mock.patch('dados.coletores.desmatamento.ALERTAS_POR_PAGINA', 2), \
             mock.patch('dados.coletores.desmatamento.baixar', side_effect=baixar):
            call_command('coletar', fonte=['desmatamento'], stdout=mock.MagicMock())
        coleta = Coleta.objects.get(fonte='desmatamento')
        self.assertTrue(coleta.sucesso)
        self.assertEqual(coleta.registros, 2)
        self.assertIn('Amazônia: HTTP 502', coleta.erro)

    def test_data_limite_usa_o_ultimo_alerta_gravado(self):
        from dados.coletores.desmatamento import data_limite

        hoje = date(2026, 10, 30)
        self.assertEqual(data_limite('Amazônia', hoje), date(2026, 9, 30))
        AlertaDesmatamento.objects.create(
            id_externo='Amazônia:x', bioma='Amazônia', classe='DEGRADACAO', data=date(2026, 10, 20),
            area_km2=1, latitude=0, longitude=0, geometria=QUADRADO,
        )
        self.assertEqual(data_limite('Amazônia', hoje), date(2026, 10, 5))
        self.assertEqual(data_limite('Cerrado', hoje), date(2026, 9, 30))

    def test_desmatamento_parcial_grava_o_bioma_que_respondeu(self):
        def baixar(url, **kwargs):
            if 'cerrado' in url:
                raise ErroDeColeta('HTTP 502')
            return RespostaFalsa(dados={'features': [feature_deter()]})

        with mock.patch('dados.coletores.desmatamento.baixar', side_effect=baixar):
            call_command('coletar', fonte=['desmatamento'], stdout=mock.MagicMock())
        coleta = Coleta.objects.get(fonte='desmatamento')
        self.assertTrue(coleta.sucesso)
        self.assertEqual(coleta.registros, 1)
        self.assertIn('Cerrado', coleta.erro)
        self.assertEqual(AlertaDesmatamento.objects.get().data, date(2026, 10, 23))


LISTAGEM_INPE = (
    '<tr><td><a href="/goes/goes19/goes19_web/tiles/">Parent Directory</a></td></tr>'
    '<tr><td><a href="202610301800/">202610301800/</a></td></tr>'
    '<tr><td><a href="202610301820/">202610301820/</a></td></tr>'
    '<tr><td><a href="202610301810/">202610301810/</a></td></tr>'
)


class SateliteTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_tile_de_conferencia_e_classes(self):
        from dados import satelite

        self.assertEqual(satelite.tile_de_conferencia(), (8, 93, 116))  # Brasília, numeração TMS
        self.assertEqual(satelite.cinza_da_temperatura(-20), 157)
        self.assertEqual(satelite.cinza_da_temperatura(-90), 255)
        cinzas = [c['cinza'] for c in satelite.classes_para_o_mapa()]
        self.assertEqual(cinzas, sorted(cinzas))  # mais frio = tom mais claro

    def test_usa_o_horario_mais_recente_completo(self):
        pedidos = []

        def baixar(url, **kwargs):
            pedidos.append(url)
            if url.endswith('/tiles/S11161113_tiles/'):
                return RespostaFalsa(LISTAGEM_INPE)
            if '202610301820/' in url:  # pasta criada, tiles ainda sendo gravados
                raise ErroDeColeta('HTTP 404')
            return RespostaFalsa()

        with mock.patch('dados.satelite.baixar', side_effect=baixar):
            resposta = self.client.get(reverse('dados:satelite')).json()
            self.client.get(reverse('dados:satelite'))  # segunda vez vem do cache
        self.assertEqual(resposta['horario'], '202610301810')
        self.assertEqual(resposta['horarios'], ['202610301800', '202610301810'])
        self.assertEqual(resposta['atual'], '2026-10-30T18:10:00+00:00')
        self.assertIn('{horario}/{z}/{x}/{y}.png', resposta['tiles'])
        self.assertEqual(len(pedidos), 2)

    def test_horario_sem_o_zoom_baixo_ainda_nao_conta(self):
        def baixar(url, **kwargs):
            if url.endswith('/tiles/S11161113_tiles/'):
                return RespostaFalsa(LISTAGEM_INPE)
            if '202610301820/4/' in url:  # o zoom 8 já foi gravado, o 4 ainda não
                raise ErroDeColeta('HTTP 404')
            return RespostaFalsa()

        with mock.patch('dados.satelite.baixar', side_effect=baixar):
            resposta = self.client.get(reverse('dados:satelite')).json()
        self.assertEqual(resposta['horario'], '202610301810')

    def test_inpe_fora_do_ar(self):
        with mock.patch('dados.satelite.baixar', side_effect=ErroDeColeta('HTTP 503')):
            resposta = self.client.get(reverse('dados:satelite'))
        self.assertEqual(resposta.status_code, 502)
        self.assertIn('HTTP 503', resposta.json()['erro'])


def grib_eta(u, v, lat_sul=-20.0, lon_oeste=-50.0, passo=1.0, extras=8, campos=None):
    """GRIB1 como o do Eta: `extras` mensagens antes, depois u (parâmetro 9) e v (10).
    `u` e `v`: listas de linhas, do sul para o norte. `campos`, se dado, substitui tudo:
    lista de (parâmetro, nível em hPa ou None, valores)."""
    import eccodes

    nj, ni = len(u), len(u[0])
    conteudo = b''
    if campos is None:
        campos = [(p, None, [[0.0] * ni] * nj) for p in range(1, extras + 1)] + [(9, None, u), (10, None, v), (11, None, u)]
    for parametro, nivel, valores in campos:
        h = eccodes.codes_grib_new_from_samples('regular_ll_sfc_grib1')
        for chave, valor in [
            ('Ni', ni), ('Nj', nj), ('jScansPositively', 1),
            ('latitudeOfFirstGridPointInDegrees', lat_sul), ('longitudeOfFirstGridPointInDegrees', lon_oeste),
            ('latitudeOfLastGridPointInDegrees', lat_sul + (nj - 1) * passo),
            ('longitudeOfLastGridPointInDegrees', lon_oeste + (ni - 1) * passo),
            ('iDirectionIncrementInDegrees', passo), ('jDirectionIncrementInDegrees', passo),
            ('indicatorOfParameter', parametro),
        ] + ([('indicatorOfTypeOfLevel', 100), ('level', nivel)] if nivel else []):
            eccodes.codes_set(h, chave, valor)
        eccodes.codes_set_values(h, [float(x) for linha in valores for x in linha])
        conteudo += eccodes.codes_get_message(h)
        eccodes.codes_release(h)
    return conteudo


class VentoTest(TestCase):
    U = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    V = [[-1.0, -2.0, -3.0], [-4.0, -5.0, -6.0]]

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_interpreta_u_e_v_pelos_parametros_do_eta(self):
        from dados.vento import interpretar

        grade = interpretar(grib_eta(self.U, self.V), limites=(-49.0, -19.0, -48.0, -19.0))
        self.assertEqual((grade['lat_sul'], grade['lon_oeste'], grade['passo']), (-20.0, -50.0, 1.0))
        self.assertEqual(grade['u'], self.U)
        self.assertEqual(grade['v'], self.V)

    def test_trecho_cortado_antes_do_vento(self):
        from dados.vento import interpretar

        conteudo = grib_eta(self.U, self.V)
        with self.assertRaises(ErroDeColeta):
            interpretar(conteudo[: len(conteudo) // 2])

    def test_rodadas_candidatas(self):
        from datetime import datetime, timezone as tz

        from dados.vento import rodadas_candidatas

        valido = datetime(2026, 10, 30, 9, tzinfo=tz.utc)
        self.assertEqual(
            [r.strftime('%d %H') for r in rodadas_candidatas(valido)],
            ['30 00', '29 12', '29 00', '28 12', '28 00'],
        )

    def test_rota_usa_a_rodada_mais_recente_disponivel_e_pede_so_o_comeco(self):
        pedidos = []
        grib = grib_eta(self.U, self.V, lat_sul=-16.0, lon_oeste=-49.0)

        class Parcial(RespostaFalsa):
            status_code = 206

        def baixar(url, cabecalhos=None, **kwargs):
            pedidos.append((url, cabecalhos))
            if '/12/eta_40km' in url:  # a rodada das 12 UTC ainda não saiu
                raise ErroDeColeta('HTTP 404')
            return Parcial(conteudo=grib)

        agora = timezone.now().replace(hour=15, minute=40)
        with mock.patch('dados.vento.baixar', side_effect=baixar), \
             mock.patch('dados.vento.datetime') as relogio:
            relogio.now.return_value = agora
            resposta = self.client.get(reverse('dados:vento')).json()
            self.client.get(reverse('dados:vento'))  # do cache
        self.assertEqual(len(pedidos), 2)
        self.assertEqual(pedidos[1][1], {'Range': 'bytes=0-1048575'})
        self.assertIn(f"eta_40km_{agora:%Y%m%d}00+{agora:%Y%m%d}15.grb", pedidos[1][0])
        self.assertEqual(resposta['valido_em'], agora.replace(minute=0, second=0, microsecond=0).isoformat())
        self.assertEqual(resposta['u'], self.U)

    def test_500_hpa_pega_so_o_nivel_certo(self):
        from dados.vento import interpretar

        outro = [[9.0] * 3] * 2
        grib = grib_eta(self.U, self.V, campos=[
            (56, 850, outro), (56, 500, self.U), (56, 250, outro),
            (57, 850, outro), (57, 500, self.V), (57, 250, outro),
        ])
        grade = interpretar(grib, limites=(-49.0, -19.0, -48.0, -19.0), camada='500hPa')
        self.assertEqual((grade['u'], grade['v']), (self.U, self.V))
        with self.assertRaises(ErroDeColeta):  # sem os parâmetros 9 e 10, não há vento a 10 m
            interpretar(grib)

    def test_rota_500_hpa_pede_o_trecho_do_meio(self):
        pedidos = []
        grib = grib_eta(self.U, self.V, lat_sul=-16.0, lon_oeste=-49.0,
                        campos=[(56, 500, self.U), (57, 500, self.V)])

        def baixar(url, cabecalhos=None, **kwargs):
            pedidos.append(cabecalhos)
            return RespostaFalsa(conteudo=grib)

        with mock.patch('dados.vento.baixar', side_effect=baixar):
            resposta = self.client.get(reverse('dados:vento'), {'camada': '500hPa'}).json()
        self.assertEqual(pedidos[0], {'Range': 'bytes=3500000-5300000'})
        self.assertEqual((resposta['camada'], resposta['v']), ('500hPa', self.V))

    def test_camada_invalida(self):
        self.assertEqual(self.client.get(reverse('dados:vento'), {'camada': '850hPa'}).status_code, 400)

    def test_cptec_fora_do_ar(self):
        with mock.patch('dados.vento.baixar', side_effect=ErroDeColeta('HTTP 503')):
            resposta = self.client.get(reverse('dados:vento'))
        self.assertEqual(resposta.status_code, 502)


class PrevisaoChuvaTest(TestCase):
    MM_POR_HORA = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]  # linhas do sul para o norte
    GRAUS = [[30.0, 31.0, 32.0], [33.0, 34.0, 35.0]]

    def grib_chuva(self, mm=None, lat_sul=-20.0, lon_oeste=-50.0):
        """GRIB como o do Eta com a temperatura máxima (parâmetro 6, em K) e a chuva da hora (13, em m)."""
        mm = mm or self.MM_POR_HORA
        kelvin = [[c + 273.15 for c in linha] for linha in self.GRAUS]
        return grib_eta(mm, mm, lat_sul=lat_sul, lon_oeste=lon_oeste, campos=[
            (6, None, kelvin), (12, None, mm), (13, None, [[x / 1000 for x in linha] for linha in mm]),
        ])

    def test_le_chuva_e_temperatura_mesmo_com_lixo_antes_da_mensagem(self):
        from dados.previsao import campos_da_hora

        # a janela baixada começa no meio de outra mensagem, que pode ter "GRIB" nos dados
        geometria, mm, graus = campos_da_hora(b'\x00GRIB\x07lixo' + self.grib_chuva())
        self.assertEqual((geometria['lat_sul'], geometria['lon_oeste'], geometria['passo']), (-20.0, -50.0, 1.0))
        self.assertEqual(mm.round(3).tolist(), self.MM_POR_HORA)
        self.assertEqual(graus.round(1).tolist(), self.GRAUS)
        self.assertEqual(campos_da_hora(grib_eta(self.MM_POR_HORA, self.MM_POR_HORA, extras=0))[1:], (None, None))

    def test_coleta_a_rodada_completa_mais_recente_uma_vez(self):
        from datetime import datetime, timezone as tz

        from dados import previsao
        from dados.models import PrevisaoChuva, PrevisaoTemperatura

        pedidos = []
        grib = self.grib_chuva(lat_sul=-16.0, lon_oeste=-49.0)

        class Parcial(RespostaFalsa):
            status_code = 206

        def baixar(url, cabecalhos=None, **kwargs):
            pedidos.append(url)
            if '/31/12/' in url:  # a rodada das 12 UTC ainda não terminou de sair
                raise ErroDeColeta('HTTP 404')
            return Parcial(conteudo=grib)

        agora = datetime(2026, 10, 31, 15, 40, tzinfo=tz.utc)
        with mock.patch('dados.previsao.baixar', side_effect=baixar):
            self.assertEqual(previsao.coletar(agora), previsao.HORAS_GUARDADAS)
            self.assertEqual(previsao.coletar(agora), 0)  # a mesma rodada não é baixada de novo
        guardada = PrevisaoChuva.objects.get()
        self.assertEqual(guardada.rodada, datetime(2026, 10, 31, 0, tzinfo=tz.utc))
        self.assertEqual(guardada.primeira_hora, datetime(2026, 10, 31, 1, tzinfo=tz.utc))
        self.assertEqual(guardada.valores().shape, (previsao.HORAS_GUARDADAS, 2, 3))
        self.assertEqual(guardada.valores()[-1].tolist(), self.MM_POR_HORA)
        temperatura = PrevisaoTemperatura.objects.get()
        self.assertEqual((temperatura.rodada, temperatura.horas), (guardada.rodada, guardada.horas))
        self.assertEqual(temperatura.valores()[0].tolist(), self.GRAUS)
        self.assertEqual(len(pedidos), 2 + previsao.HORAS_GUARDADAS + 2)  # 2 conferências por coleta

    def previsao_com(self, valor_por_hora, rodada, horas=108, modelo=None, padrao=0.0):
        """Previsão 2×2 sobre o prédio do BC em Recife, com o mesmo valor em toda a grade a cada hora."""
        import zlib

        import numpy as np

        from dados.cidades import CIDADES
        from dados.models import PrevisaoChuva

        bc = next(c for c in CIDADES if c['id'] == 'recife')['bc']
        grade = np.full((horas, 2, 2), padrao, dtype='<f4')
        for hora, valor in valor_por_hora.items():
            grade[hora] = valor
        return (modelo or PrevisaoChuva).objects.create(
            rodada=rodada, primeira_hora=rodada + timedelta(hours=1), horas=horas,
            lat_sul=bc['lat'] - 0.2, lon_oeste=bc['lon'] - 0.2, passo=0.4, linhas=2, colunas=2,
            grade=zlib.compress(grade.tobytes()),
        )

    def test_indicador_soma_a_partir_de_agora_e_acha_a_pior_janela(self):
        from datetime import datetime, timezone as tz

        from dados import previsao, situacao
        from dados.cidades import CIDADES

        rodada = datetime(2026, 10, 31, 0, tzinfo=tz.utc)
        agora = rodada + timedelta(hours=10, minutes=30)
        # índice 9 (hora que termina 10h UTC) já passou; de 10 em diante é o futuro
        chuva = {9: 50.0, 10: 2.0, **{k: 3.0 for k in range(36, 56)}}  # 24–48 h são os índices 34 a 57
        self.previsao_com(chuva, rodada)
        recife = next(c for c in CIDADES if c['id'] == 'recife')
        indicador = situacao.chuva_prevista(recife, previsao.proximas_horas(agora), agora)
        self.assertEqual(indicador['dias_mm'], [2.0, 60.0, 0.0])  # 0–24 h, 24–48 h, 48–72 h
        self.assertEqual(indicador['nivel'], 'amarelo')  # 60 mm numa janela de 24 h
        self.assertIn('62 mm nas próximas 72 h, até 60 mm em 24 h', indicador['texto'])
        self.assertEqual(indicador['dados_de'], rodada.isoformat())

    def test_rotas_da_camada_somam_os_proximos_dias(self):
        rodada = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
        self.previsao_com({hora: 1.0 for hora in range(108)}, rodada)  # 1 mm/h em toda a grade
        dados = self.client.get(reverse('dados:chuva_prevista'), {'dias': 2}).json()
        self.assertEqual(dados['dias'], 2)
        self.assertEqual((dados['media_mm'], dados['maximo_mm']), (48.0, 48.0))
        self.assertEqual(dados['rodada'], rodada.isoformat())
        imagem = self.client.get(dados['imagem'])
        self.assertEqual(imagem['Content-Type'], 'image/png')
        ponto = self.client.get(reverse('dados:chuva_prevista_no_ponto'), {'lat': -8.06, 'lon': -34.88, 'dias': 3}).json()
        self.assertEqual(ponto['mm'], 72.0)
        fora = self.client.get(reverse('dados:chuva_prevista_no_ponto'), {'lat': -8.06, 'lon': -30.0}).json()
        self.assertIsNone(fora['mm'])  # no mar, fora da grade e do Brasil
        self.assertEqual(self.client.get(reverse('dados:chuva_prevista'), {'dias': 4}).status_code, 400)

    def test_rotas_da_temperatura_pegam_a_maior_do_periodo(self):
        from dados.models import PrevisaoTemperatura

        rodada = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
        # 30 °C em geral; 36 °C numa hora do 2º dia (índice ~30), que só entra a partir de 48 h
        self.previsao_com({30: 36.0}, rodada, modelo=PrevisaoTemperatura, padrao=30.0)
        um_dia = self.client.get(reverse('dados:temperatura_prevista'), {'dias': 1}).json()
        dois_dias = self.client.get(reverse('dados:temperatura_prevista'), {'dias': 2}).json()
        self.assertEqual(um_dia['maximo_c'], 30.0)
        self.assertEqual(dois_dias['maximo_c'], 36.0)
        self.assertEqual(self.client.get(dois_dias['imagem'])['Content-Type'], 'image/png')
        ponto = self.client.get(reverse('dados:temperatura_prevista_no_ponto'), {'lat': -8.06, 'lon': -34.88, 'dias': 2}).json()
        self.assertEqual(ponto['graus'], 36.0)

    def test_rota_da_camada_sem_previsao(self):
        self.assertEqual(self.client.get(reverse('dados:chuva_prevista')).json(), {'dias': 0})
        self.assertEqual(self.client.get(reverse('dados:chuva_prevista_png')).status_code, 404)

    def test_calor_contra_a_normal_do_mes(self):
        from datetime import datetime, timezone as tz

        from dados import previsao, situacao
        from dados.cidades import CIDADES
        from dados.models import PrevisaoTemperatura

        recife = next(c for c in CIDADES if c['id'] == 'recife')  # normal de novembro: 30,4 °C
        rodada = datetime(2026, 11, 10, 0, tzinfo=tz.utc)
        agora = rodada + timedelta(hours=10, minutes=30)  # o índice 10 é a primeira hora futura

        def calor(graus_por_hora):
            PrevisaoTemperatura.objects.all().delete()
            self.previsao_com(graus_por_hora, rodada, modelo=PrevisaoTemperatura, padrao=30.0)
            return situacao.calor_previsto(recife, previsao.proximas_horas(agora, PrevisaoTemperatura), agora)

        normal = calor({9: 45.0})  # a hora que já passou não conta
        self.assertEqual((normal['nivel'], normal['dias_c'], normal['normais_c']), ('verde', [30.0, 30.0, 30.0], [30.4] * 3))
        um_dia = calor({40: 36.0})  # 24–48 h são os índices 34 a 57
        self.assertEqual(um_dia['nivel'], 'amarelo')
        self.assertEqual(um_dia['texto'], 'Máxima de 36 °C (normal de novembro: 30,4 °C); 6 °C acima da normal')
        onda = calor({40: 36.0, 60: 35.5})
        self.assertEqual(onda['nivel'], 'vermelho')
        self.assertIn('2 dias 5 °C ou mais acima da normal: onda de calor', onda['texto'])
        self.assertEqual(calor({12: 40.5})['nivel'], 'vermelho')  # 40 °C é extremo em qualquer capital

    def test_previsao_velha_demais_fica_indisponivel(self):
        from datetime import datetime, timezone as tz

        from dados import previsao, situacao
        from dados.cidades import CIDADES

        rodada = datetime(2026, 10, 29, 0, tzinfo=tz.utc)
        self.previsao_com({}, rodada)
        agora = rodada + timedelta(hours=40)  # sobram menos de 72 h na rodada guardada
        self.assertIsNone(previsao.proximas_horas(agora))
        indicador = situacao.chuva_prevista(CIDADES[0], None, agora)
        self.assertEqual(indicador['nivel'], 'indisponivel')


def imagem_glm(raios=(), passo=0.1, lon0=-80.0, lat0=10.0, tamanho=600):
    """JPG como o do GLM no INPE: fundo preto, barra de cores (0 a 500) no rodapé e um
    quadrado de 3×3 pixels por raio, na cor da densidade. `raios`: [(lat, lon, densidade)]."""
    import io

    from PIL import Image, ImageDraw

    imagem = Image.new('RGB', (tamanho, tamanho))
    desenho = ImageDraw.Draw(imagem)
    x0, x1 = 100, 500

    def cor(valor):  # roxo → vermelho → amarelo → branco, como a do INPE
        t = valor / 500
        pontos = [(0, (78, 7, 161)), (0.05, (220, 40, 60)), (0.1, (255, 176, 1)), (0.25, (255, 251, 29)), (1, (255, 255, 253))]
        for (ta, ca), (tb, cb) in zip(pontos, pontos[1:]):
            if t <= tb:
                f = (t - ta) / (tb - ta)
                return tuple(round(a + (b - a) * f) for a, b in zip(ca, cb))
        return pontos[-1][1]

    for x in range(x0, x1 + 1):
        desenho.line([(x, tamanho - 30), (x, tamanho - 20)], fill=cor(500 * (x - x0) / (x1 - x0)))
    for lat, lon, densidade in raios:
        c, r = round((lon - lon0) / passo), round((lat0 - lat) / passo)
        desenho.rectangle([c - 1, r - 1, c + 1, r + 1], fill=cor(densidade))
    saida = io.BytesIO()
    imagem.save(saida, format='JPEG', quality=95)
    return saida.getvalue()


class RaiosTest(TestCase):
    GEOMETRIA = (0.1, -80.0, 10.0)
    JGW = '0.1\n0.0\n0.0\n-0.1\n-80.0\n10.0\n'

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_le_densidade_pela_barra_e_fica_so_no_brasil(self):
        from dados import raios
        from dados.coletores.geo import SIGLAS_DA_MASCARA

        # Acima de ~125 a barra do INPE só muda no azul, que o JPEG borra: o teste fica abaixo disso.
        conteudo = imagem_glm([(-15.8, -47.9, 100), (-23.5, -46.6, 20), (-34.9, -56.2, 400)])  # o último é Montevidéu
        celulas = raios.interpretar(conteudo, self.GEOMETRIA)
        self.assertEqual({SIGLAS_DA_MASCARA[c[3]] for c in celulas}, {'DF', 'SP'})
        mais_forte = max(celulas, key=lambda c: c[2])
        self.assertAlmostEqual(mais_forte[0], -15.8, delta=0.15)
        self.assertAlmostEqual(mais_forte[1], -47.9, delta=0.15)
        self.assertAlmostEqual(mais_forte[2], 100, delta=20)
        self.assertAlmostEqual(max(c[2] for c in celulas if SIGLAS_DA_MASCARA[c[3]] == 'SP'), 20, delta=10)

    def test_jgw_e_listagem(self):
        from dados import raios

        self.assertEqual(raios.interpretar_jgw(self.JGW), self.GEOMETRIA)
        html = '<a href="GLM_202610311520.jgw">x</a><a href="GLM_202610311520.jpg">x</a><a href="GLM_202610311515.jpg">'
        self.assertEqual(raios.interpretar_listagem(html), ['202610311515', '202610311520'])

    def test_rota_junta_os_quadros_e_guarda_a_idade_do_mais_recente(self):
        quadros = {
            '202610311500': imagem_glm([(-15.8, -47.9, 100), (-23.5, -46.6, 300)]),
            '202610311515': imagem_glm([(-15.8, -47.9, 30)]),
            '202610311525': imagem_glm([]),
        }
        listagem = ''.join(f'<a href="GLM_{h}.jpg">' for h in ['202610311420', *quadros])
        pedidos = []

        def baixar(url, **kwargs):
            pedidos.append(url)
            if url.endswith('/glm/'):
                return RespostaFalsa(listagem)
            if url.endswith('.jgw'):
                return RespostaFalsa(self.JGW)
            return RespostaFalsa(conteudo=quadros[url[-16:-4]])

        with mock.patch('dados.raios.baixar', side_effect=baixar):
            dados = self.client.get(reverse('dados:raios'), {'minutos': 30}).json()
            self.client.get(reverse('dados:raios'), {'minutos': 30})  # tudo do cache
            so_df = self.client.get(reverse('dados:raios'), {'minutos': 30, 'uf': 'DF'}).json()
        self.assertEqual((dados['ultimo'], dados['quadros']), ('2026-10-31T15:25:00+00:00', 3))
        no_df = [c for c in dados['celulas'] if round(c[0]) == -16]
        em_sp = [c for c in dados['celulas'] if round(c[0]) == -23]
        self.assertEqual({c[3] for c in no_df}, {10})  # o raio mais recente no DF foi 10 min antes da última imagem
        self.assertGreater(max(c[2] for c in no_df), 70)  # a densidade é a maior da janela, não a mais recente
        self.assertEqual({c[3] for c in em_sp}, {25})
        self.assertEqual(dados['celulas'][0][3], 25)  # as mais antigas primeiro: as novas são desenhadas por cima
        self.assertEqual(len(pedidos), 5)  # listagem, .jgw e 3 imagens; a 2ª chamada não baixa nada
        self.assertEqual({round(c[0]) for c in so_df['celulas']}, {-16})

    def test_janela_invalida(self):
        self.assertEqual(self.client.get(reverse('dados:raios'), {'minutos': 45}).status_code, 400)


class RiosTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    @staticmethod
    def leituras(niveis, fim, passo_min=15):
        """Leituras da ANA (da mais nova à mais velha) com os níveis dados, do mais velho ao mais novo."""
        return [(fim - timedelta(minutes=passo_min * k), n) for k, n in enumerate(reversed(niveis))]

    def test_referencia_pelos_percentis_do_ano(self):
        from dados import rios

        agora = timezone.now()
        ano = self.leituras([i / 1000 for i in range(2000)], agora)  # 0,000 a 1,999 m
        ref = rios.referencia(ano)
        self.assertEqual((ref['p50'], ref['p95'], ref['p99'], ref['maximo']), (1.0, 1.9, 1.98, 2.0))
        with self.assertRaises(ErroDeColeta):
            rios.referencia(ano[:500])

    def test_resumo_usa_a_mediana_das_3_ultimas_e_a_variacao_em_6_h(self):
        from dados import rios

        agora = timezone.now().replace(minute=50, second=0, microsecond=0)
        niveis = [1.0] * 40 + [1.5, 9.0, 1.6]  # o 9,0 é um pico do sensor
        resumo = rios.resumir(self.leituras(niveis, agora), {'p50': 1, 'p95': 2, 'p99': 3}, agora)
        self.assertEqual(resumo['atual']['metros'], 1.6)
        self.assertEqual(resumo['variacao_6h'], 0.6)
        self.assertLess(len(resumo['serie']), len(niveis))  # uma leitura por hora
        self.assertEqual(resumo['serie'][-1]['metros'], 1.6)
        with self.assertRaises(ErroDeColeta):  # leitura velha demais
            rios.resumir(self.leituras(niveis, agora - timedelta(hours=8)), {}, agora)

    def test_indicador_e_rota(self):
        from dados import situacao
        from dados.cidades import CIDADES

        agora = timezone.now()
        ano = self.leituras([i / 1000 for i in range(2000)], agora)  # p95 1,90 m e p99 1,98 m

        def leituras_da_ana(codigo, dias, agora_=None):
            return ano if dias > 7 else self.leituras([1.2] * 30 + [1.95] * 3, agora)

        recife = next(c for c in CIDADES if c['id'] == 'recife')
        with mock.patch('dados.rios.leituras_da_ana', side_effect=leituras_da_ana):
            indicador = situacao.rio_perto(recife, agora)
            dados = self.client.get(reverse('dados:rio'), {'cidade': 'recife'}).json()
        self.assertEqual(indicador['nivel'], 'amarelo')
        self.assertEqual(indicador['texto'], 'Rio Capibaribe: 1,95 m, acima de 95% das leituras do último ano · subiu 75 cm em 6 h')
        self.assertEqual((dados['estacao']['codigo'], dados['atual']['metros']), ('39187800', 1.95))
        self.assertEqual(self.client.get(reverse('dados:rio'), {'cidade': 'sao-paulo'}).status_code, 400)


class EnchenteApiTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_rota_traz_relevo_referencias_e_nivel_atual(self):
        xml = ('<DataTable><DadosHidrometereologicos><DataHora>2026-10-30 16:00:00</DataHora>'
               '<Nivel>131.00</Nivel></DadosHidrometereologicos></DataTable>')
        with mock.patch('dados.enchente.baixar', return_value=RespostaFalsa(xml)):
            resposta = self.client.get(reverse('dados:enchente')).json()
        self.assertEqual(resposta['nivel']['atual']['metros'], 1.31)
        self.assertTrue(resposta['arquivos']['relevo'].endswith('relevo_guaiba.bin'))
        self.assertEqual(set(resposta['arquivos']), {'relevo', 'alcance', 'municipios', 'observado', 'diques'})
        self.assertEqual(resposta['referencias'][-1], {'metros': 5.37, 'nome': 'Maio de 2024'})
        self.assertEqual(len(resposta['sementes']), 4)
        self.assertEqual(resposta['municipios'][0]['nome'], 'Porto Alegre')

    def test_ana_fora_do_ar_nao_derruba_o_simulador(self):
        with mock.patch('dados.enchente.baixar', side_effect=ErroDeColeta('HTTP 503')):
            resposta = self.client.get(reverse('dados:enchente'))
        self.assertEqual(resposta.status_code, 200)
        self.assertIn('HTTP 503', resposta.json()['nivel']['erro'])


class EnchenteBelemTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def xml_mare(self, horas=72):
        import math
        from datetime import datetime, timedelta

        agora = datetime.now().replace(minute=0, second=0, microsecond=0)
        linhas = []
        for i in range(horas * 4):
            quando = agora - timedelta(minutes=15 * i)
            nivel = 640 + 150 * math.cos(2 * math.pi * (15 * i) / (12.42 * 60))  # cm, média 6,40 m
            linhas.append(f'<DadosHidrometereologicos><DataHora>{quando:%Y-%m-%d %H:%M:%S}</DataHora>'
                          f'<Nivel>{nivel:.2f}</Nivel></DadosHidrometereologicos>')
        return '<DataTable>' + ''.join(linhas) + '</DataTable>'

    def test_mare_relativa_a_media_e_mares_altas(self):
        with mock.patch('dados.enchente.baixar', return_value=RespostaFalsa(self.xml_mare())):
            resposta = self.client.get(reverse('dados:enchente_belem')).json()
        mare = resposta['mare']
        self.assertAlmostEqual(mare['media_30d'], 6.4, places=1)
        self.assertAlmostEqual(mare['atual']['relativo'], 1.5, places=1)  # agora é maré alta (ainda não confirmada)
        self.assertAlmostEqual(mare['mares_altas']['maxima'], 1.5, places=1)
        self.assertGreaterEqual(mare['mares_altas']['quantidade'], 5)
        self.assertTrue(resposta['setores'].endswith('belem_setores_risco.geojson'))
        self.assertEqual(resposta['estacao']['codigo'], '31645000')

    def test_ana_fora_do_ar_mantem_as_areas_de_risco(self):
        with mock.patch('dados.enchente.baixar', side_effect=ErroDeColeta('HTTP 503')):
            resposta = self.client.get(reverse('dados:enchente_belem'))
        self.assertEqual(resposta.status_code, 200)
        self.assertIn('HTTP 503', resposta.json()['mare']['erro'])


def regional(cidade='recife', **niveis):
    """Regional com os indicadores pedidos, ex.: regional(chuva='amarelo')."""
    return {'id': cidade, 'nome': cidade, 'uf': 'PE', 'nivel': 'verde', 'indicadores': [
        {'id': chave, 'nome': chave, 'nivel': nivel, 'texto': f'{chave} {nivel}', 'dados_de': None}
        for chave, nivel in niveis.items()
    ]}


class AlertasRegionaisTest(TestCase):
    def test_abre_escala_ignora_indisponivel_e_fecha(self):
        from dados.models import AlertaRegional
        from dados.situacao import registrar_alertas

        t0 = timezone.now()
        registrar_alertas([regional(chuva='amarelo', vento='verde')], t0)
        alerta = AlertaRegional.objects.get()
        self.assertEqual((alerta.indicador, alerta.nivel, alerta.nivel_maximo, alerta.fim), ('chuva', 'amarelo', 'amarelo', None))

        registrar_alertas([regional(chuva='vermelho')], t0 + timedelta(minutes=30))
        registrar_alertas([regional(chuva='amarelo')], t0 + timedelta(minutes=60))
        alerta.refresh_from_db()
        self.assertEqual((alerta.nivel, alerta.nivel_maximo, alerta.texto_pico), ('amarelo', 'vermelho', 'chuva vermelho'))

        registrar_alertas([regional(chuva='indisponivel')], t0 + timedelta(minutes=90))
        alerta.refresh_from_db()
        self.assertIsNone(alerta.fim)  # fonte fora do ar não encerra o alerta

        registrar_alertas([regional(chuva='verde')], t0 + timedelta(minutes=120))
        alerta.refresh_from_db()
        self.assertEqual(alerta.fim, t0 + timedelta(minutes=120))

        registrar_alertas([regional(chuva='amarelo')], t0 + timedelta(minutes=150))
        self.assertEqual(AlertaRegional.objects.count(), 2)  # nova ocorrência, novo alerta

    def test_rota_de_alertas_filtra_por_cidade(self):
        from dados.situacao import registrar_alertas

        t0 = timezone.now()
        registrar_alertas([regional('recife', chuva='amarelo'), regional('belem', vento='vermelho')], t0)
        registrar_alertas([regional('recife', chuva='verde')], t0 + timedelta(hours=1))
        todos = self.client.get(reverse('dados:alertas')).json()
        self.assertEqual([a['cidade'] for a in todos['abertos']], ['belem'])
        self.assertEqual([a['cidade'] for a in todos['historico']], ['recife'])
        recife = self.client.get(reverse('dados:alertas'), {'cidade': 'recife'}).json()
        self.assertEqual((recife['abertos'], len(recife['historico'])), ([], 1))
        for params in ({'cidade': 'atlantida'}, {'limite': 0}, {'limite': 'x'}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(reverse('dados:alertas'), params).status_code, 400)


class IndicadoresTest(TestCase):
    def setUp(self):
        from dados.cidades import CIDADES

        self.recife = next(c for c in CIDADES if c['id'] == 'recife')
        self.agora = timezone.now()
        for fonte in ('queimadas', 'sismos'):
            Coleta.objects.create(fonte=fonte, iniciada_em=self.agora, concluida_em=self.agora, sucesso=True)

    def foco(self, id_, km_ao_norte):
        bc = self.recife['bc']
        FocoQueimada.objects.create(id_inpe=id_, data_hora=self.agora - timedelta(hours=2),
                                    latitude=bc['lat'] + km_ao_norte / 111.2, longitude=bc['lon'],
                                    satelite='NOAA-20', uf='PE')

    def test_queimadas(self):
        from dados import situacao

        self.assertEqual(situacao.queimadas(self.recife, self.agora)['nivel'], 'verde')
        self.foco('longe', 20)
        self.foco('perto', 8)
        indicador = situacao.queimadas(self.recife, self.agora)
        self.assertEqual(indicador['nivel'], 'amarelo')
        self.assertIn('1 foco', indicador['texto'])
        self.foco('colado', 2)
        self.assertEqual(situacao.queimadas(self.recife, self.agora)['nivel'], 'vermelho')

    def test_coleta_velha_deixa_indisponivel(self):
        from dados import situacao

        Coleta.objects.update(concluida_em=self.agora - timedelta(days=3))
        self.assertEqual(situacao.queimadas(self.recife, self.agora)['nivel'], 'indisponivel')
        self.assertEqual(situacao.sismos(self.recife, self.agora)['nivel'], 'indisponivel')

    def test_sismos(self):
        from dados import situacao

        bc = self.recife['bc']
        Sismo.objects.create(id_evento='a', data_hora=self.agora - timedelta(days=1), latitude=bc['lat'] + 0.5,
                             longitude=bc['lon'], magnitude=2.8, uf='PE')  # ~55 km
        self.assertEqual(situacao.sismos(self.recife, self.agora)['nivel'], 'amarelo')
        Sismo.objects.create(id_evento='b', data_hora=self.agora - timedelta(days=1), latitude=bc['lat'] + 1.5,
                             longitude=bc['lon'], magnitude=4.2, uf='PB')  # ~167 km
        indicador = situacao.sismos(self.recife, self.agora)
        self.assertEqual(indicador['nivel'], 'vermelho')
        self.assertIn('Magnitude 4,2', indicador['texto'])

    def test_raios_por_distancia_e_idade(self):
        from dados import situacao

        bc = self.recife['bc']
        ultimo = self.agora - timedelta(minutes=10)

        def dados(*celulas):  # (km ao norte do prédio, minutos antes da última imagem)
            return {'ultimo': ultimo.isoformat(),
                    'celulas': [[bc['lat'] + km / 111.2, bc['lon'], 20, idade] for km, idade in celulas]}

        self.assertEqual(situacao.raios_perto(self.recife, dados((35, 0)), self.agora)['nivel'], 'verde')
        indicador = situacao.raios_perto(self.recife, dados((18, 40), (15, 5)), self.agora)
        self.assertEqual(indicador['nivel'], 'amarelo')  # perto, mas não a até 10 km
        self.assertEqual(indicador['texto'], 'Raio a 15 km há 15 min (2 áreas com raios a até 20 km)')
        self.assertEqual(situacao.raios_perto(self.recife, dados((8, 30)), self.agora)['nivel'], 'amarelo')  # já faz tempo
        self.assertEqual(situacao.raios_perto(self.recife, dados((8, 10)), self.agora)['nivel'], 'vermelho')
        atrasado = {**dados((8, 10)), 'ultimo': (self.agora - timedelta(minutes=45)).isoformat()}
        self.assertEqual(situacao.raios_perto(self.recife, atrasado, self.agora)['nivel'], 'indisponivel')

    def test_chuva_e_vento(self):
        from dados import situacao
        from dados.chuva import Acumulado
        from dados.coletores.chuva import gravar

        gravar({'data': timezone.localdate(), **grade_brasil(42.0)})
        indicador = situacao.chuva(self.recife, Acumulado(1), self.agora)
        self.assertEqual((indicador['nivel'], indicador['texto'][:5]), ('amarelo', '42 mm'))
        grade = {'lat_sul': -10.0, 'lon_oeste': -36.0, 'passo': 1.0, 'linhas': 3, 'colunas': 3,
                 'u': [[-10.0] * 3] * 3, 'v': [[0.0] * 3] * 3, 'valido_em': self.agora.isoformat()}
        vento = situacao.vento_previsto(self.recife, grade, self.agora)  # 36 km/h vindo de leste
        self.assertEqual(vento['nivel'], 'amarelo')
        self.assertIn('vindo de L', vento['texto'])

    def test_guaiba_e_mare(self):
        from dados import situacao
        from dados.chuva import Acumulado

        leitura = {'atual': {'quando': self.agora.isoformat(), 'metros': 2.7}}
        with mock.patch('dados.enchente.nivel_do_guaiba', return_value=leitura):
            self.assertEqual(situacao.guaiba(self.agora)['nivel'], 'amarelo')
        mare = {'atual': {'quando': self.agora.isoformat(), 'relativo': 1.6},
                'mares_altas': {'p90': 1.54, 'recentes': []}}
        with mock.patch('dados.enchente.mare_de_belem', return_value=mare):
            self.assertEqual(situacao.mare_belem(Acumulado(1), self.agora)['nivel'], 'amarelo')  # sem chuva

    def test_situacao_atual_reaproveita_avaliacao_recente(self):
        from dados import situacao

        with mock.patch('dados.situacao.avaliar', return_value=[regional(chuva='vermelho')]) as avaliar:
            primeira = self.client.get(reverse('dados:situacao')).json()
            self.client.get(reverse('dados:situacao'))
            self.assertEqual(avaliar.call_count, 1)
            situacao.situacao_atual(timezone.now() + timedelta(minutes=11))
            self.assertEqual(avaliar.call_count, 2)
        self.assertEqual(primeira['regionais'][0]['indicadores'][0]['nivel'], 'vermelho')
        self.assertIn('tempestade', primeira['limiares'])


def aviso_inmet(id_, evento='Tempestade', severidade='Perigo Potencial', inicio='2026-10-30 08:00',
                fim='2026-10-30 23:59', geocodes='2611606,2607901', encerrado=False, codigo=None):
    return {
        'id': id_, 'codigo': codigo or f'urn:{id_}', 'descricao': evento, 'severidade': severidade,
        'aviso_cor': '#FFFE00', 'inicio': inicio, 'fim': fim, 'estados': 'Pernambuco', 'encerrado': encerrado,
        'riscos': ['Chuva de 20 a 30 mm/h.'], 'instrucoes': ['Evite enfrentar o mau tempo.'], 'geocodes': geocodes,
        'poligono': '{"type":"Polygon","coordinates":[[[-35,-8],[-34,-8],[-34,-9],[-35,-8]]]}',
    }


class AvisosInmetTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_interpreta_descarta_encerrados_e_repetidos(self):
        from dados import avisos

        dados = {
            'hoje': [aviso_inmet(1), aviso_inmet(2, encerrado=True)],
            'futuro': [aviso_inmet(3, codigo='urn:1'), aviso_inmet(4, severidade='Perigo', inicio='2026-11-02 00:00', fim='2026-11-02 23:59')],
        }
        lista = avisos.interpretar(dados)
        self.assertEqual([a['id'] for a in lista], [1, 4])
        self.assertEqual(lista[0]['inicio'].isoformat(), '2026-10-30T08:00:00-03:00')
        self.assertEqual(lista[0]['geocodes'], ['2611606', '2607901'])
        self.assertEqual(lista[0]['poligono']['type'], 'Polygon')

    def test_indicador_e_rota(self):
        from datetime import datetime

        from dados import avisos, situacao
        from dados.cidades import CIDADES

        recife = next(c for c in CIDADES if c['id'] == 'recife')
        lista = avisos.interpretar({'hoje': [aviso_inmet(1)], 'futuro': [
            aviso_inmet(4, severidade='Perigo', inicio='2026-11-02 00:00', fim='2026-11-02 23:59')]})
        dentro = datetime(2026, 10, 30, 12, tzinfo=avisos.HORARIO_DE_BRASILIA)
        indicador = situacao.aviso_inmet(recife, lista, dentro)
        self.assertEqual(indicador['nivel'], 'amarelo')
        self.assertIn('Tempestade (perigo potencial) até 23:59 de 30/10', indicador['texto'])
        depois = datetime(2026, 10, 31, 12, tzinfo=avisos.HORARIO_DE_BRASILIA)
        indicador = situacao.aviso_inmet(recife, lista, depois)
        self.assertEqual(indicador['nivel'], 'verde')
        self.assertIn('próximo: Tempestade (perigo) em 02/11', indicador['texto'])
        belem = next(c for c in CIDADES if c['id'] == 'belem')
        self.assertEqual(situacao.aviso_inmet(belem, lista, dentro)['texto'], 'Nenhum aviso em vigor')

        bruto = {'hoje': [aviso_inmet(1, inicio='2020-01-01 00:00', fim='2099-12-31 23:59')], 'futuro': [
            aviso_inmet(4, inicio='2099-01-01 00:00', fim='2099-01-01 23:59')]}
        with mock.patch('dados.avisos.baixar', return_value=RespostaFalsa(dados=bruto)):
            agora = self.client.get(reverse('dados:avisos')).json()
            proximos = self.client.get(reverse('dados:avisos'), {'quando': 'proximos'}).json()
        self.assertEqual([f['properties']['id'] for f in agora['features']], [1])
        self.assertEqual([f['properties']['id'] for f in proximos['features']], [4])
        self.assertNotIn('geocodes', agora['features'][0]['properties'])
        self.assertEqual(self.client.get(reverse('dados:avisos'), {'quando': 'ontem'}).status_code, 400)


class SaudeTest(TestCase):
    REGISTRO = {'SE': 202642, 'data_iniSE': 1792281600000, 'casos_est': 300.0, 'casos': 107, 'p_inc100k': 4.5,
                'nivel': 3, 'Rt': 1.01}

    def test_interpreta_e_coleta(self):
        from dados.coletores import saude
        from dados.models import BoletimSaude

        boletins = saude.interpretar([self.REGISTRO, {**self.REGISTRO, 'nivel': 9}, {'SE': 'x'}], 'recife', 'dengue')
        self.assertEqual(len(boletins), 1)
        self.assertEqual(boletins[0]['inicio_semana'].isoformat(), '2026-10-18')
        self.assertEqual((boletins[0]['casos'], boletins[0]['incidencia'], boletins[0]['nivel']), (107, 4.5, 3))

        with mock.patch('dados.coletores.saude.baixar', return_value=RespostaFalsa(dados=[self.REGISTRO])):
            call_command('coletar', fonte=['saude'], stdout=mock.MagicMock())
            call_command('coletar', fonte=['saude'], stdout=mock.MagicMock())
        from dados.cidades import CIDADES

        self.assertEqual(BoletimSaude.objects.count(), len(CIDADES) * len(saude.DOENCAS))  # sem duplicar

    def test_indicador_e_rota(self):
        from datetime import date

        from dados import situacao
        from dados.cidades import CIDADES
        from dados.models import BoletimSaude

        agora = timezone.now()
        Coleta.objects.create(fonte='saude', iniciada_em=agora, concluida_em=agora, sucesso=True)
        recife = next(c for c in CIDADES if c['id'] == 'recife')
        for semana, nivel, incidencia in ((202641, 2, 3.0), (202642, 4, 30.0)):
            BoletimSaude.objects.create(cidade='recife', doenca='dengue', semana=semana, inicio_semana=date(2026, 10, 18),
                                        nivel=nivel, incidencia=incidencia)
        BoletimSaude.objects.create(cidade='recife', doenca='chikungunya', semana=202642, inicio_semana=date(2026, 10, 18), nivel=1)
        indicador = situacao.saude(recife, agora)
        self.assertEqual(indicador['nivel'], 'vermelho')
        self.assertIn('Dengue: acima do limiar epidêmico, 30,0 casos/100 mil (semana 42)', indicador['texto'])

        tabela = self.client.get(reverse('dados:saude')).json()
        linha = next(c for c in tabela['cidades'] if c['id'] == 'recife')
        self.assertEqual((linha['doencas']['dengue']['nivel'], linha['doencas']['dengue']['incidencia_anterior']), (4, 3.0))
        self.assertEqual(next(c for c in tabela['cidades'] if c['id'] == 'belem')['doencas'], {})


def alerta_cemaden(cod, codibge='2611606', municipio='RECIFE', evento='Movimentos de Massa - Moderado', nivel='Moderado',
                   status=1, criado='2026-10-30 10:43:24.431'):
    return {'cod_alerta': cod, 'datahoracriacao': criado, 'ult_atualizacao': criado, 'codibge': int(codibge),
            'evento': evento, 'nivel': nivel, 'status': status, 'uf': 'PE', 'municipio': municipio,
            'latitude': -8.05, 'longitude': -34.88}


class CemadenTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_interpreta_ativos_do_mais_grave_ao_mais_leve(self):
        from dados import cemaden

        lista = cemaden.interpretar({'alertas': [
            alerta_cemaden(1, criado='2026-10-29 08:00:00.000'),
            alerta_cemaden(2, evento='Risco Hidrológico - Alto', nivel='Alto', municipio='SÃO JOSÉ DOS CAMPOS'),
            alerta_cemaden(3, status=2),  # não está em vigor
        ]})
        self.assertEqual([a['codigo'] for a in lista], [2, 1])
        self.assertEqual((lista[0]['tipo'], lista[0]['nivel'], lista[0]['municipio']), ('Risco Hidrológico', 'Alto', 'São José dos Campos'))
        self.assertEqual(lista[1]['criado_em'].isoformat(), '2026-10-29T08:00:00-03:00')

    def test_indicador_da_regional(self):
        from dados import cemaden, situacao
        from dados.cidades import CIDADES

        recife = next(c for c in CIDADES if c['id'] == 'recife')
        agora = timezone.now()
        moderado = cemaden.interpretar({'alertas': [alerta_cemaden(1)]})
        indicador = situacao.alerta_cemaden(recife, moderado, agora)
        self.assertEqual(indicador['nivel'], 'amarelo')
        self.assertEqual(indicador['texto'], 'Movimentos de Massa (moderado) desde 30/10 10:43')
        alto = cemaden.interpretar({'alertas': [alerta_cemaden(1), alerta_cemaden(2, evento='Risco Hidrológico - Muito Alto', nivel='Muito Alto')]})
        indicador = situacao.alerta_cemaden(recife, alto, agora)
        self.assertEqual(indicador['nivel'], 'vermelho')
        self.assertIn('também movimentos de massa', indicador['texto'])
        belem = next(c for c in CIDADES if c['id'] == 'belem')
        self.assertEqual(situacao.alerta_cemaden(belem, alto, agora)['nivel'], 'verde')

    def test_rota_e_fonte_fora_do_ar(self):
        teresopolis = alerta_cemaden(8, codibge='3305802', municipio='TERESÓPOLIS')  # fora das regionais
        with mock.patch('dados.cemaden.baixar', return_value=RespostaFalsa(dados={'alertas': [alerta_cemaden(7), teresopolis]})):
            geojson = self.client.get(reverse('dados:cemaden')).json()
        [feature] = geojson['features']
        self.assertEqual(feature['geometry']['coordinates'], [-34.88, -8.05])
        self.assertEqual((feature['properties']['municipio'], feature['properties']['cor']), ('Recife', '#f59f00'))
        from django.core.cache import cache

        cache.clear()
        with mock.patch('dados.cemaden.baixar', side_effect=ErroDeColeta('HTTP 503')):
            self.assertEqual(self.client.get(reverse('dados:cemaden')).status_code, 502)
