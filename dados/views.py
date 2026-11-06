import json
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from django.db.models import Count, Max, Sum
from django.db.models.functions import Round
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

from . import avisos, cemaden, enchente, previsao, raios, rios, satelite, situacao, vento
from .chuva import CLASSES, Acumulado
from .cidades import CIDADES
from .coletores.http import ErroDeColeta
from .coletores.desmatamento import nome_da_classe
from .coletores.geo import uf_do_ponto
from .coletores.ufs import NOME_POR_SIGLA, SIGLAS, UFS
from .management.commands.coletar import FONTES
from .coletores.saude import DOENCAS
from .models import AlertaDesmatamento, AlertaRegional, BoletimSaude, Coleta, FocoQueimada, Sismo

# Acima disso o navegador sofre para desenhar; os mais recentes têm prioridade.
LIMITE_FOCOS = 40_000
LIMITE_ALERTAS = 10_000
# Mapa de calor: focos somados em células de 0,1° (~11 km), sem limite de período.
CASAS_DA_GRADE = 1


class FiltroInvalido(ValueError):
    pass


def _dias(request, parametro, padrao):
    """Lê um período em dias (1 a 365) da query string."""
    try:
        dias = int(request.GET.get(parametro, padrao))
    except ValueError as erro:
        raise FiltroInvalido(f'{parametro} deve ser um número inteiro') from erro
    if not 1 <= dias <= 365:
        raise FiltroInvalido(f'{parametro} deve estar entre 1 e 365')
    return dias


def _filtro(request, dias_padrao):
    """Lê ?dias= (1 a 365) e ?uf= (sigla ou vazio) da requisição."""
    dias = _dias(request, 'dias', dias_padrao)
    uf = request.GET.get('uf', '').upper()
    if uf and uf not in SIGLAS:
        raise FiltroInvalido(f'UF desconhecida: {uf}')
    return dias, uf


def _com_filtro(view):
    """Converte FiltroInvalido em resposta 400."""
    def envolvida(request):
        try:
            return view(request)
        except FiltroInvalido as erro:
            return JsonResponse({'erro': str(erro)}, status=400)
    envolvida.__name__ = view.__name__
    return envolvida


def _ponto(lon, lat, propriedades):
    return {
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': [round(lon, 5), round(lat, 5)]},
        'properties': propriedades,
    }


def _colecao(features, truncado=False):
    return JsonResponse({'type': 'FeatureCollection', 'truncado': truncado, 'features': features})


def _versao_estaticos():
    """Data da última alteração do mapa.js e do mapa.css: vai no endereço deles, para o navegador
    não continuar usando a versão antiga do cache depois de uma mudança."""
    pasta = Path(__file__).resolve().parent / 'static' / 'dados'
    return int(max((pasta / nome).stat().st_mtime for nome in ('mapa.js', 'mapa.css')))


def mapa(request):
    return render(request, 'dados/mapa.html', {
        'versao_estaticos': _versao_estaticos(),
        'ufs': sorted(UFS, key=lambda u: u[2]),
        'classes_chuva': [{'mm': limite, 'cor': cor} for limite, cor in CLASSES],
        'classes_temperatura': [{'graus': limite, 'cor': cor} for limite, cor in previsao.CLASSES_TEMPERATURA],
        'classes_nuvens': satelite.classes_para_o_mapa(),
        'cidades': CIDADES,
        'classes_vento': [{'kmh': limite, 'cor': cor} for limite, cor in vento.CLASSES],
    })


def enchente_guaiba(request):
    """Metadados do relevo do simulador, endereço dos arquivos e o nível atual do Guaíba (ANA)."""
    if not enchente.ARQUIVO_META.exists():
        return JsonResponse({'erro': 'relevo não preparado: rode python manage.py preparar_enchente'}, status=503)
    meta = json.loads(enchente.ARQUIVO_META.read_text(encoding='utf-8'))
    try:
        nivel = enchente.nivel_do_guaiba()
    except ErroDeColeta as erro:
        nivel = {'erro': f'ANA indisponível: {erro}'}
    return JsonResponse({
        **meta,
        'arquivos': {
            nome: static('dados/enchente/' + arquivo.name) for nome, arquivo in [
                ('relevo', enchente.ARQUIVO_RELEVO), ('alcance', enchente.ARQUIVO_ALCANCE),
                ('municipios', enchente.ARQUIVO_MUNICIPIOS), ('observado', enchente.ARQUIVO_OBSERVADO),
                ('diques', enchente.ARQUIVO_DIQUES),
            ]
        },
        'nivel': nivel,
        'estacao': {'codigo': enchente.ESTACAO_CAIS_MAUA, 'nome': 'Cais Mauá C6 (ANA)'},
        'referencias': enchente.REFERENCIAS,
        'nivel_normal': enchente.NIVEL_NORMAL,
        'nivel_maximo': enchente.NIVEL_MAXIMO,
    })


def enchente_belem(request):
    """Maré de Belém (ANA, 30 dias) e o endereço das áreas de risco mapeadas pelo SGB."""
    if not enchente.ARQUIVO_SETORES_BELEM.exists():
        return JsonResponse({'erro': 'áreas de risco não preparadas: rode python manage.py preparar_belem'}, status=503)
    try:
        mare = enchente.mare_de_belem()
    except ErroDeColeta as erro:
        mare = {'erro': f'ANA indisponível: {erro}'}
    return JsonResponse({
        'mare': mare,
        'estacao': {'codigo': enchente.ESTACAO_BELEM, 'nome': "Captação d'água da Cosanpa (ANA)"},
        'setores': static('dados/enchente/' + enchente.ARQUIVO_SETORES_BELEM.name),
        'centro': enchente.CENTRO_BELEM,
    })


def situacao_regionais(request):
    """Situação de cada regional do BC (avaliação de até 10 min atrás) e os limiares usados."""
    avaliacao = situacao.situacao_atual()
    return JsonResponse({
        'avaliado_em': avaliacao.momento.isoformat(),
        'regionais': avaliacao.regionais,
        'limiares': situacao.LIMIARES,
    })


def _alerta_json(alerta):
    return {
        'id': alerta.id, 'cidade': alerta.cidade, 'indicador': alerta.indicador,
        'nome_indicador': situacao.NOMES.get(alerta.indicador, alerta.indicador),
        'nivel': alerta.nivel, 'nivel_maximo': alerta.nivel_maximo,
        'inicio': alerta.inicio.isoformat(), 'fim': alerta.fim.isoformat() if alerta.fim else None,
        'atualizado_em': alerta.atualizado_em.isoformat(), 'texto': alerta.texto, 'texto_pico': alerta.texto_pico,
    }


@_com_filtro
def alertas_regionais(request):
    """Alertas abertos e o histórico (?cidade= para uma regional; ?limite= de 1 a 500, padrão 50)."""
    try:
        limite = int(request.GET.get('limite', 50))
    except ValueError as erro:
        raise FiltroInvalido('limite deve ser um número inteiro') from erro
    if not 1 <= limite <= 500:
        raise FiltroInvalido('limite deve estar entre 1 e 500')
    consulta = AlertaRegional.objects.all()
    cidade = request.GET.get('cidade', '')
    if cidade:
        if cidade not in {c['id'] for c in CIDADES}:
            raise FiltroInvalido(f'cidade desconhecida: {cidade}')
        consulta = consulta.filter(cidade=cidade)
    return JsonResponse({
        'abertos': [_alerta_json(a) for a in consulta.filter(fim__isnull=True).order_by('-inicio')],
        'historico': [_alerta_json(a) for a in consulta.filter(fim__isnull=False).order_by('-fim')[:limite]],
    })


@_com_filtro
def avisos_inmet(request):
    """Avisos do INMET como GeoJSON: ?quando=agora (em vigor, padrão), proximos ou todos."""
    quando = request.GET.get('quando', 'agora')
    if quando not in ('agora', 'proximos', 'todos'):
        raise FiltroInvalido('quando deve ser agora, proximos ou todos')
    try:
        lista = avisos.avisos_ativos()
    except ErroDeColeta as erro:
        return JsonResponse({'erro': f'INMET indisponível: {erro}'}, status=502)
    agora = timezone.now()
    if quando == 'agora':
        lista = [a for a in lista if avisos.em_vigor(a, agora)]
    elif quando == 'proximos':
        lista = [a for a in lista if a['inicio'] > agora]
    return JsonResponse(avisos.como_geojson(lista))


def alertas_cemaden(request):
    """Alertas de risco geo-hidrológico do CEMADEN em vigor nos municípios das regionais do BC, como GeoJSON."""
    regionais = {c['ibge'] for c in CIDADES}
    try:
        return JsonResponse(cemaden.como_geojson([a for a in cemaden.alertas_ativos() if a['codibge'] in regionais]))
    except ErroDeColeta as erro:
        return JsonResponse({'erro': f'CEMADEN indisponível: {erro}'}, status=502)


def saude_regionais(request):
    """Últimos boletins do InfoDengue de cada cidade em foco, com a semana anterior para a tendência."""
    cidades = []
    for cidade in CIDADES:
        doencas = {}
        for doenca, nome in DOENCAS.items():
            ultimos = list(BoletimSaude.objects.filter(cidade=cidade['id'], doenca=doenca).order_by('-semana')[:2])
            if not ultimos:
                continue
            atual = ultimos[0]
            anterior = ultimos[1] if len(ultimos) > 1 else None
            doencas[doenca] = {
                'nome': nome, 'semana': atual.semana, 'inicio_semana': atual.inicio_semana.isoformat(),
                'nivel': atual.nivel, 'casos': atual.casos, 'casos_estimados': atual.casos_estimados,
                'incidencia': atual.incidencia, 'rt': atual.rt,
                'incidencia_anterior': anterior.incidencia if anterior else None,
            }
        cidades.append({'id': cidade['id'], 'nome': cidade['nome'], 'uf': cidade['uf'], 'doencas': doencas})
    return JsonResponse({'cidades': cidades, 'doencas': DOENCAS})


@_com_filtro
def vento_10m(request):
    """Vento (u e v em m/s) da hora atual, previsão do modelo Eta do CPTEC: ?camada=10m (padrão,
    as setas) ou 500hPa (desloca a projeção das nuvens)."""
    camada = request.GET.get('camada', '10m')
    if camada not in vento.CAMADAS:
        raise FiltroInvalido(f'camada deve ser uma de: {", ".join(vento.CAMADAS)}')
    try:
        return JsonResponse(vento.vento_agora(camada=camada))
    except ErroDeColeta as erro:
        return JsonResponse({'erro': f'CPTEC indisponível: {erro}'}, status=502)


def satelite_goes(request):
    """Horários disponíveis das imagens do GOES-19 no INPE e o endereço dos tiles."""
    try:
        disponiveis = satelite.horarios_disponiveis()
    except ErroDeColeta as erro:
        return JsonResponse({'erro': f'INPE indisponível: {erro}'}, status=502)
    return JsonResponse({
        'atual': satelite.como_datetime(disponiveis['atual']).isoformat(),
        'horario': disponiveis['atual'],
        'horarios': disponiveis['horarios'],
        'tiles': satelite.URL_TILES + '{horario}/{z}/{x}/{y}.png',
        'tms': True,
        'zoom_maximo': satelite.ZOOM_MAXIMO,
        'limites': satelite.LIMITES,
    })


@_com_filtro
def focos(request):
    dias, uf = _filtro(request, dias_padrao=1)
    consulta = FocoQueimada.objects.filter(data_hora__gte=timezone.now() - timedelta(days=dias))
    if uf:
        consulta = consulta.filter(uf=uf)
    linhas = list(consulta.order_by('-data_hora').values_list(
        'longitude', 'latitude', 'data_hora', 'municipio', 'uf', 'bioma', 'satelite', 'frp',
    )[:LIMITE_FOCOS + 1])
    features = [
        _ponto(lon, lat, {
            'data_hora': data_hora.isoformat(),
            'municipio': municipio, 'uf': sigla, 'bioma': bioma,
            'satelite': satelite, 'frp': frp,
        })
        for lon, lat, data_hora, municipio, sigla, bioma, satelite, frp in linhas[:LIMITE_FOCOS]
    ]
    return _colecao(features, truncado=len(linhas) > LIMITE_FOCOS)


@_com_filtro
def focos_calor(request):
    """Focos contados por célula da grade: [[lat, lon, focos], ...] para o mapa de calor."""
    dias, uf = _filtro(request, dias_padrao=1)
    consulta = FocoQueimada.objects.filter(data_hora__gte=timezone.now() - timedelta(days=dias))
    if uf:
        consulta = consulta.filter(uf=uf)
    celulas = (
        consulta
        .annotate(lat=Round('latitude', CASAS_DA_GRADE), lon=Round('longitude', CASAS_DA_GRADE))
        .values('lat', 'lon')
        .annotate(n=Count('id'))
        .values_list('lat', 'lon', 'n')
    )
    celulas = [[round(lat, CASAS_DA_GRADE), round(lon, CASAS_DA_GRADE), n] for lat, lon, n in celulas]
    return JsonResponse({
        'celulas': celulas,
        'focos': sum(n for _, _, n in celulas),
        'maximo': max((n for _, _, n in celulas), default=0),
        'tamanho_grau': 10 ** -CASAS_DA_GRADE,
    })


@_com_filtro
def chuva(request):
    """Período coberto, limites e endereço da imagem da chuva acumulada nos últimos `dias` gravados."""
    dias, uf = _filtro(request, dias_padrao=1)
    acumulado = Acumulado(dias)
    if acumulado.vazio:
        return JsonResponse({'dias': 0})
    area = acumulado.area(uf)
    valores = acumulado.mm[area]
    parametros = {'dias': dias, 'fim': acumulado.fim.isoformat()}  # `fim` renova o cache da imagem
    if uf:
        parametros['uf'] = uf
    return JsonResponse({
        'inicio': acumulado.inicio.isoformat(),
        'fim': acumulado.fim.isoformat(),
        'dias': acumulado.dias,
        'limites': acumulado.limites(),
        'imagem': f"{reverse('dados:chuva_png')}?{urlencode(parametros)}",
        'media_mm': round(float(valores.mean()), 1) if valores.size else None,
        'maximo_mm': round(float(valores.max()), 1) if valores.size else None,
    })


@_com_filtro
def chuva_png(request):
    dias, uf = _filtro(request, dias_padrao=1)
    acumulado = Acumulado(dias)
    if acumulado.vazio:
        return HttpResponse(status=404)
    resposta = HttpResponse(acumulado.png(uf), content_type='image/png')
    resposta['Cache-Control'] = 'max-age=3600'
    return resposta


@_com_filtro
def chuva_no_ponto(request):
    """mm acumulados na célula de ?lat=&lon=, para o popup do clique no mapa."""
    dias = _dias(request, 'dias', 1)
    try:
        latitude = float(request.GET['lat'])
        longitude = float(request.GET['lon'])
    except (KeyError, ValueError) as erro:
        raise FiltroInvalido('lat e lon são obrigatórios e numéricos') from erro
    acumulado = Acumulado(dias)
    if acumulado.vazio:
        return JsonResponse({'mm': None})
    return JsonResponse({
        'mm': acumulado.no_ponto(latitude, longitude),
        'uf': uf_do_ponto(latitude, longitude),
        'inicio': acumulado.inicio.isoformat(),
        'fim': acumulado.fim.isoformat(),
    })


@_com_filtro
def rio_da_cidade(request):
    """Nível do rio monitorado perto do prédio do BC em ?cidade= (recife, belo-horizonte, rio-de-janeiro, curitiba)."""
    cidade = request.GET.get('cidade', '')
    if cidade not in rios.ESTACOES:
        raise FiltroInvalido(f'cidade deve ser uma de: {", ".join(rios.ESTACOES)}')
    try:
        return JsonResponse(rios.situacao_do_rio(cidade))
    except ErroDeColeta as erro:
        return JsonResponse({'erro': f'ANA indisponível: {erro}'}, status=502)


@_com_filtro
def raios_recentes(request):
    """Raios do GLM (GOES-19) nos últimos ?minutos= (15, 30 ou 60) em células de 0,1°."""
    _, uf = _filtro(request, dias_padrao=1)
    try:
        minutos = int(request.GET.get('minutos', 30))
    except ValueError as erro:
        raise FiltroInvalido('minutos deve ser um número inteiro') from erro
    if minutos not in raios.JANELAS_MIN:
        raise FiltroInvalido(f'minutos deve ser um de: {", ".join(map(str, raios.JANELAS_MIN))}')
    try:
        return JsonResponse(raios.recentes(minutos, uf))
    except ErroDeColeta as erro:
        return JsonResponse({'erro': f'INPE indisponível: {erro}'}, status=502)


def _filtro_previsao(request):
    dias, uf = _filtro(request, dias_padrao=1)
    if dias > previsao.HORAS // 24:
        raise FiltroInvalido(f'dias deve estar entre 1 e {previsao.HORAS // 24} na previsão')
    return dias, uf


# Camadas da previsão do Eta: a grade (previsao.Prevista ou MaximaPrevista), a rota da imagem e
# a unidade nos nomes das chaves (media_mm/maximo_mm e mm na chuva; media_c/maximo_c e graus na temperatura).
CAMADAS_PREVISTAS = {
    'chuva': (previsao.Prevista, 'dados:chuva_prevista_png', 'mm', 'mm'),
    'temperatura': (previsao.MaximaPrevista, 'dados:temperatura_prevista_png', 'c', 'graus'),
}


def _camada_prevista(request, nome):
    """Rodada, período, estatísticas e endereço da imagem da camada prevista nos próximos `dias` (1 a 3)."""
    grade, rota_png, unidade, _ = CAMADAS_PREVISTAS[nome]
    dias, uf = _filtro_previsao(request)
    prevista = grade(dias)
    if prevista.vazio:
        return JsonResponse({'dias': 0})
    # `inicio` muda a cada hora e renova o cache da imagem
    parametros = {'dias': dias, 'inicio': f'{prevista.inicio:%Y%m%d%H}'}
    if uf:
        parametros['uf'] = uf
    media, maximo = prevista.estatisticas(uf)
    return JsonResponse({
        'rodada': prevista.rodada.isoformat(),
        'inicio': prevista.inicio.isoformat(),
        'fim': prevista.fim.isoformat(),
        'dias': dias,
        'limites': prevista.limites(),
        'imagem': f"{reverse(rota_png)}?{urlencode(parametros)}",
        f'media_{unidade}': media,
        f'maximo_{unidade}': maximo,
    })


def _camada_prevista_png(request, nome):
    dias, uf = _filtro_previsao(request)
    prevista = CAMADAS_PREVISTAS[nome][0](dias)
    if prevista.vazio:
        return HttpResponse(status=404)
    resposta = HttpResponse(prevista.png(uf), content_type='image/png')
    resposta['Cache-Control'] = 'max-age=3600'
    return resposta


def _camada_prevista_no_ponto(request, nome):
    """Valor previsto nos próximos `dias` na célula de ?lat=&lon=, para o popup do clique no mapa."""
    grade, _, _, chave = CAMADAS_PREVISTAS[nome]
    dias, _ = _filtro_previsao(request)
    try:
        latitude = float(request.GET['lat'])
        longitude = float(request.GET['lon'])
    except (KeyError, ValueError) as erro:
        raise FiltroInvalido('lat e lon são obrigatórios e numéricos') from erro
    prevista = grade(dias)
    if prevista.vazio:
        return JsonResponse({chave: None})
    return JsonResponse({
        chave: prevista.no_ponto(latitude, longitude),
        'inicio': prevista.inicio.isoformat(),
        'fim': prevista.fim.isoformat(),
        'rodada': prevista.rodada.isoformat(),
    })


@_com_filtro
def chuva_prevista(request):
    return _camada_prevista(request, 'chuva')


@_com_filtro
def chuva_prevista_png(request):
    return _camada_prevista_png(request, 'chuva')


@_com_filtro
def chuva_prevista_no_ponto(request):
    return _camada_prevista_no_ponto(request, 'chuva')


@_com_filtro
def temperatura_prevista(request):
    return _camada_prevista(request, 'temperatura')


@_com_filtro
def temperatura_prevista_png(request):
    return _camada_prevista_png(request, 'temperatura')


@_com_filtro
def temperatura_prevista_no_ponto(request):
    return _camada_prevista_no_ponto(request, 'temperatura')


@_com_filtro
def desmatamento(request):
    dias, uf = _filtro(request, dias_padrao=30)
    consulta = AlertaDesmatamento.objects.filter(data__gte=timezone.localdate() - timedelta(days=dias))
    if uf:
        consulta = consulta.filter(uf=uf)
    alertas = list(consulta.order_by('-data').values(
        'geometria', 'data', 'classe', 'area_km2', 'municipio', 'uf', 'bioma', 'satelite',
    )[:LIMITE_ALERTAS + 1])
    features = [
        {
            'type': 'Feature',
            'geometry': alerta['geometria'],
            'properties': {
                'data': alerta['data'].isoformat(),
                'classe': nome_da_classe(alerta['classe']),
                'area_km2': round(alerta['area_km2'], 3),
                'municipio': alerta['municipio'], 'uf': alerta['uf'],
                'bioma': alerta['bioma'], 'satelite': alerta['satelite'],
            },
        }
        for alerta in alertas[:LIMITE_ALERTAS]
    ]
    return _colecao(features, truncado=len(alertas) > LIMITE_ALERTAS)


@_com_filtro
def sismos(request):
    dias, uf = _filtro(request, dias_padrao=90)
    consulta = Sismo.objects.filter(data_hora__gte=timezone.now() - timedelta(days=dias))
    if uf:
        consulta = consulta.filter(uf=uf)
    features = [
        _ponto(s.longitude, s.latitude, {
            'data_hora': s.data_hora.isoformat(), 'magnitude': s.magnitude,
            'tipo_magnitude': s.tipo_magnitude, 'profundidade_km': s.profundidade_km,
            'local': s.local, 'uf': s.uf,
        })
        for s in consulta.order_by('-data_hora')
    ]
    return _colecao(features)


@_com_filtro
def resumo(request):
    """Totais por UF de cada camada (cada uma com seu período) e a última coleta de cada fonte."""
    dias_focos, uf = _filtro(request, dias_padrao=1)
    dias_alertas = _dias(request, 'dias_desmatamento', 30)
    dias_sismos = _dias(request, 'dias_sismos', 90)
    dias_chuva = _dias(request, 'dias_chuva', 1)
    agora = timezone.now()

    focos_q = FocoQueimada.objects.filter(data_hora__gte=agora - timedelta(days=dias_focos))
    alertas_q = AlertaDesmatamento.objects.filter(data__gte=timezone.localdate() - timedelta(days=dias_alertas))
    sismos_q = Sismo.objects.filter(data_hora__gte=agora - timedelta(days=dias_sismos))
    if uf:
        focos_q, alertas_q, sismos_q = (q.filter(uf=uf) for q in (focos_q, alertas_q, sismos_q))

    por_uf = {}
    def linha(sigla):
        return por_uf.setdefault(sigla, {
            'uf': sigla, 'nome': NOME_POR_SIGLA.get(sigla, 'Sem UF'),
            'focos': 0, 'alertas': 0, 'area_km2': 0.0, 'sismos': 0, 'chuva_mm': None,
        })
    for item in focos_q.values('uf').annotate(n=Count('id')):
        linha(item['uf'])['focos'] = item['n']
    for item in alertas_q.values('uf').annotate(n=Count('id'), area=Sum('area_km2')):
        registro = linha(item['uf'])
        registro['alertas'] = item['n']
        registro['area_km2'] = round(item['area'] or 0, 2)
    for item in sismos_q.values('uf').annotate(n=Count('id')):
        linha(item['uf'])['sismos'] = item['n']
    acumulado = Acumulado(dias_chuva)
    if not acumulado.vazio:
        for sigla, mm in acumulado.media_por_uf().items():
            if not uf or sigla == uf:
                linha(sigla)['chuva_mm'] = mm

    coletas = {}
    for fonte in FONTES:
        ultima = Coleta.objects.filter(fonte=fonte).first()
        ultimo_sucesso = Coleta.objects.filter(fonte=fonte, sucesso=True).aggregate(m=Max('concluida_em'))['m']
        coletas[fonte] = {
            'ultima_tentativa': ultima.iniciada_em.isoformat() if ultima else None,
            'em_andamento': bool(ultima and ultima.concluida_em is None),
            'sucesso': ultima.sucesso if ultima and ultima.concluida_em else None,
            'erro': ultima.erro if ultima else '',
            'ultimo_sucesso': ultimo_sucesso.isoformat() if ultimo_sucesso else None,
        }

    return JsonResponse({
        'ufs': sorted(por_uf.values(), key=lambda r: (-r['focos'], -r['area_km2'], r['uf'])),
        'coletas': coletas,
    })
