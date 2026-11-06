"""Alertas de risco geo-hidrológico do CEMADEN (painelalertas.cemaden.gov.br).

O CEMADEN (Centro Nacional de Monitoramento e Alertas de Desastres Naturais, MCTI)
emite alertas de risco hidrológico (enchente, enxurrada) e de movimentos de massa
(deslizamento) para os municípios que monitora; eles vão para a Defesa Civil Nacional e
para a Defesa Civil do município. O painel público expõe os alertas em vigor em JSON,
cada um com o código IBGE do município. Nada vai para o banco: cache de 10 min.
"""

from datetime import datetime

from django.core.cache import cache

from .coletores.http import ErroDeColeta, baixar
from .coletores.ufs import titulo
from .enchente import HORARIO_DE_BRASILIA

URL_ALERTAS = 'https://painelalertas.cemaden.gov.br/wsAlertas2'
CACHE_SEGUNDOS = 10 * 60
# Nível do CEMADEN → nível na situação das regionais.
NIVEL_DO_ALERTA = {'Moderado': 'amarelo', 'Alto': 'vermelho', 'Muito Alto': 'vermelho'}
ORDEM_DO_NIVEL = {'Moderado': 1, 'Alto': 2, 'Muito Alto': 3}
CORES = {'Moderado': '#f59f00', 'Alto': '#e8590c', 'Muito Alto': '#c92a2a'}


def _quando(texto):
    """'2026-10-30 10:43:24.431' → datetime; o painel não diz o fuso, tratamos como horário de Brasília."""
    for formato in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(str(texto).strip(), formato).replace(tzinfo=HORARIO_DE_BRASILIA)
        except ValueError:
            continue
    return None


def interpretar(dados):
    """JSON do painel → alertas ativos (status 1), do mais grave ao mais leve e do mais novo ao mais velho."""
    alertas = []
    for a in dados.get('alertas') or []:
        if a.get('status') != 1:
            continue
        nivel = (a.get('nivel') or '').strip()
        evento = (a.get('evento') or '').strip()
        try:
            latitude, longitude = float(a['latitude']), float(a['longitude'])
        except (KeyError, TypeError, ValueError):
            continue
        alertas.append({
            'codigo': a.get('cod_alerta'),
            'tipo': evento.rsplit(' - ', 1)[0] if ' - ' in evento else evento,
            'nivel': nivel,
            'cor': CORES.get(nivel, '#f59f00'),
            'criado_em': _quando(a.get('datahoracriacao')),
            'atualizado_em': _quando(a.get('ult_atualizacao')),
            'codibge': str(a.get('codibge') or '').strip(),
            'municipio': titulo(a.get('municipio')),
            'uf': (a.get('uf') or '').strip(),
            'latitude': latitude,
            'longitude': longitude,
        })
    return sorted(alertas, key=lambda a: (-ORDEM_DO_NIVEL.get(a['nivel'], 0), -(a['criado_em'].timestamp() if a['criado_em'] else 0)))


def alertas_ativos():
    """Alertas em vigor (cache de 10 min). Levanta ErroDeColeta."""
    em_cache = cache.get('cemaden:alertas')
    if em_cache is not None:
        return em_cache
    try:
        dados = baixar(URL_ALERTAS, timeout=60).json()
    except ValueError as erro:
        raise ErroDeColeta('o painel de alertas do CEMADEN não devolveu JSON') from erro
    alertas = interpretar(dados)
    cache.set('cemaden:alertas', alertas, CACHE_SEGUNDOS)
    return alertas


def como_geojson(alertas):
    return {'type': 'FeatureCollection', 'features': [
        {
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [a['longitude'], a['latitude']]},
            'properties': {
                'codigo': a['codigo'], 'tipo': a['tipo'], 'nivel': a['nivel'], 'cor': a['cor'],
                'municipio': a['municipio'], 'uf': a['uf'],
                'criado_em': a['criado_em'].isoformat() if a['criado_em'] else None,
                'atualizado_em': a['atualizado_em'].isoformat() if a['atualizado_em'] else None,
            },
        }
        for a in alertas
    ]}
