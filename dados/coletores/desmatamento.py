"""Alertas de desmatamento do INPE DETER (TerraBrasilis, WFS).

O serviço tem centenas de milhares de polígonos e recusa filtros CQL com
frequência, então a coleta pagina os alertas do mais recente para o mais
antigo até uma data-limite e acumula no banco: 30 dias na primeira coleta e,
depois, 15 dias antes do alerta mais recente já gravado (o DETER publica com
atraso alertas de imagens antigas).
"""

from datetime import date, timedelta

from django.db.models import Max
from django.utils import timezone

from ..models import AlertaDesmatamento
from .geo import centro_aproximado
from .http import ColetaParcial, ErroDeColeta, baixar
from .ufs import SIGLAS

URL_WFS = 'https://terrabrasilis.dpi.inpe.br/geoserver/{workspace}/ows'

BIOMAS = [
    ('Amazônia', 'deter-amz', 'deter-amz:deter_amz'),
    ('Cerrado', 'deter-cerrado-nb', 'deter-cerrado-nb:deter_cerrado'),
]

ALERTAS_POR_PAGINA = 1000
MAX_PAGINAS = 15
JANELA_INICIAL_DIAS = 30
SOBREPOSICAO_DIAS = 15

CLASSES = {
    'DESMATAMENTO_CR': 'Desmatamento (corte raso)',
    'DESMATAMENTO_VEG': 'Desmatamento com vegetação',
    'CICATRIZ_DE_QUEIMADA': 'Cicatriz de queimada',
    'DEGRADACAO': 'Degradação',
    'CS_DESORDENADO': 'Corte seletivo desordenado',
    'CS_GEOMETRICO': 'Corte seletivo geométrico',
    'MINERACAO': 'Mineração',
}


def nome_da_classe(classe):
    return CLASSES.get(classe, classe.replace('_', ' ').capitalize())


def interpretar(geojson, bioma):
    """FeatureCollection do DETER → lista de dicts com os campos de AlertaDesmatamento."""
    features = geojson.get('features') if isinstance(geojson, dict) else None
    if not isinstance(features, list):
        raise ErroDeColeta('resposta do DETER não é um GeoJSON de alertas')
    alertas = []
    for feature in features:
        props = feature.get('properties') or {}
        geometria = feature.get('geometry')
        centro = centro_aproximado(geometria)
        try:
            data = date.fromisoformat(str(props.get('view_date'))[:10])
        except ValueError:
            continue
        if not props.get('gid') or centro is None:
            continue
        uf = str(props.get('uf') or '').upper()
        area = props.get('areamunkm')
        if area is None:
            area = props.get('areatotalkm', 0)
        alertas.append({
            'id_externo': f'{bioma}:{props["gid"]}',
            'bioma': bioma,
            'classe': str(props.get('classname') or ''),
            'data': data,
            'area_km2': float(area or 0),
            'municipio': str(props.get('municipality') or props.get('municipali') or ''),
            'uf': uf if uf in SIGLAS else '',
            'satelite': ' '.join(
                str(props.get(chave)) for chave in ('satellite', 'sensor') if props.get(chave)
            ),
            'latitude': centro[0],
            'longitude': centro[1],
            'geometria': geometria,
        })
    return alertas


def data_limite(bioma, hoje):
    """Até onde paginar: 15 dias antes do último alerta gravado, ou 30 dias atrás."""
    ultima = AlertaDesmatamento.objects.filter(bioma=bioma).aggregate(m=Max('data'))['m']
    if ultima is None:
        return hoje - timedelta(days=JANELA_INICIAL_DIAS)
    return ultima - timedelta(days=SOBREPOSICAO_DIAS)


def _pagina(workspace, camada, inicio):
    resposta = baixar(
        URL_WFS.format(workspace=workspace),
        params={
            'service': 'WFS',
            'version': '2.0.0',
            'request': 'GetFeature',
            'typeName': camada,
            'count': ALERTAS_POR_PAGINA,
            'startIndex': inicio,
            'sortBy': 'view_date D',
            'outputFormat': 'application/json',
            'srsName': 'EPSG:4326',
        },
        timeout=180,
    )
    try:
        return resposta.json()
    except ValueError as erro:
        raise ErroDeColeta('resposta do DETER não é JSON') from erro


def _coletar_bioma(bioma, workspace, camada, limite):
    """Pagina até passar da data-limite. Devolve (alertas por id, erro ou None):
    se uma página falha no meio, o que já veio é aproveitado."""
    alertas = {}
    for numero in range(MAX_PAGINAS):
        try:
            geojson = _pagina(workspace, camada, numero * ALERTAS_POR_PAGINA)
            pagina = interpretar(geojson, bioma)
        except ErroDeColeta as erro:
            return alertas, f'{bioma}: {erro}'
        for alerta in pagina:
            alertas[alerta['id_externo']] = alerta
        quantidade = len(geojson.get('features', []))
        if quantidade < ALERTAS_POR_PAGINA or not pagina:
            break
        if min(alerta['data'] for alerta in pagina) < limite:
            break
    return alertas, None


def coletar():
    """Coleta os alertas recentes de cada bioma. Um bioma fora do ar não
    impede os outros; todos fora do ar levanta ErroDeColeta."""
    hoje = timezone.localdate()
    total, falhas = 0, []
    for bioma, workspace, camada in BIOMAS:
        alertas, erro = _coletar_bioma(bioma, workspace, camada, data_limite(bioma, hoje))
        if erro:
            falhas.append(erro)
        AlertaDesmatamento.objects.bulk_create(
            [AlertaDesmatamento(**alerta) for alerta in alertas.values()],
            batch_size=500,
            update_conflicts=True,
            unique_fields=['id_externo'],
            update_fields=['classe', 'area_km2', 'municipio', 'uf', 'geometria', 'latitude', 'longitude'],
        )
        total += len(alertas)
    if total == 0 and falhas:
        raise ErroDeColeta('; '.join(falhas))
    if falhas:
        raise ColetaParcial(total, falhas)
    return total
