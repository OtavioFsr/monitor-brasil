"""Avisos meteorológicos do INMET (os do mapa avisos.inmet.gov.br).

A API devolve os avisos ativos hoje e os previstos para os próximos dias, cada um com o
polígono da área, a severidade (Perigo Potencial, Perigo, Grande Perigo), a cor oficial,
os riscos, as instruções e os códigos IBGE dos municípios atingidos. Nada vai para o banco:
os avisos ficam em cache por 10 min.
"""

import json
from datetime import datetime

from django.core.cache import cache

from .coletores.http import ErroDeColeta, baixar
from .enchente import HORARIO_DE_BRASILIA

URL_AVISOS = 'https://apiprevmet3.inmet.gov.br/avisos/ativos'
CACHE_SEGUNDOS = 10 * 60
# Severidade do INMET → nível na situação das regionais.
NIVEL_DA_SEVERIDADE = {'Perigo Potencial': 'amarelo', 'Perigo': 'vermelho', 'Grande Perigo': 'vermelho'}
ORDEM_DA_SEVERIDADE = {'Perigo Potencial': 1, 'Perigo': 2, 'Grande Perigo': 3}


def _quando(texto):
    """'2026-10-30 08:22' (horário de Brasília) → datetime com fuso; None se não der para ler."""
    try:
        return datetime.strptime(texto.strip(), '%Y-%m-%d %H:%M').replace(tzinfo=HORARIO_DE_BRASILIA)
    except (AttributeError, ValueError):
        return None


def _lista(valor):
    if isinstance(valor, list):
        return [str(v).strip() for v in valor if str(v).strip()]
    return [valor.strip()] if isinstance(valor, str) and valor.strip() else []


def interpretar(dados):
    """JSON da API → lista de avisos (dicts), dos que começam primeiro; ignora encerrados e sem polígono."""
    avisos, vistos = [], set()
    for grupo in ('hoje', 'futuro'):
        for a in dados.get(grupo) or []:
            # Um aviso que já vale hoje e segue pelos próximos dias aparece nas duas listas.
            chave = a.get('codigo') or a.get('id')
            if a.get('encerrado') or chave in vistos:
                continue
            vistos.add(chave)
            try:
                poligono = json.loads(a['poligono']) if isinstance(a.get('poligono'), str) else a.get('poligono')
            except ValueError:
                poligono = None
            inicio, fim = _quando(a.get('inicio')), _quando(a.get('fim'))
            if not poligono or not inicio or not fim:
                continue
            geocodes = [g.strip() for g in str(a.get('geocodes') or '').split(',') if g.strip()]
            avisos.append({
                'id': a.get('id'),
                'codigo': a.get('codigo') or '',
                'evento': (a.get('descricao') or '').strip(),
                'severidade': (a.get('severidade') or '').strip(),
                'cor': a.get('aviso_cor') or '#FFFE00',
                'inicio': inicio,
                'fim': fim,
                'grupo': grupo,
                'estados': ', '.join(e.strip() for e in (a.get('estados') or '').split(',') if e.strip()),
                'riscos': _lista(a.get('riscos')),
                'instrucoes': _lista(a.get('instrucoes')),
                'geocodes': geocodes,
                'poligono': poligono,
            })
    return sorted(avisos, key=lambda a: (a['inicio'], -ORDEM_DA_SEVERIDADE.get(a['severidade'], 0)))


def avisos_ativos():
    """Avisos de hoje e dos próximos dias (cache de 10 min). Levanta ErroDeColeta."""
    em_cache = cache.get('avisos:inmet')
    if em_cache is not None:
        return em_cache
    try:
        dados = baixar(URL_AVISOS, timeout=60).json()
    except ValueError as erro:
        raise ErroDeColeta('a API de avisos do INMET não devolveu JSON') from erro
    avisos = interpretar(dados)
    cache.set('avisos:inmet', avisos, CACHE_SEGUNDOS)
    return avisos


def em_vigor(aviso, momento):
    return aviso['inicio'] <= momento <= aviso['fim']


def como_geojson(avisos):
    return {'type': 'FeatureCollection', 'features': [
        {
            'type': 'Feature',
            'geometry': a['poligono'],
            'properties': {
                'id': a['id'], 'evento': a['evento'], 'severidade': a['severidade'], 'cor': a['cor'],
                'inicio': a['inicio'].isoformat(), 'fim': a['fim'].isoformat(), 'grupo': a['grupo'],
                'estados': a['estados'], 'riscos': a['riscos'], 'instrucoes': a['instrucoes'],
                'municipios': len(a['geocodes']),
            },
        }
        for a in avisos
    ]}
