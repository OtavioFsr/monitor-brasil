"""Sismos do Centro de Sismologia da USP (serviço FDSN, formato texto).

A consulta por retângulo também devolve eventos de países vizinhos e do mar;
só ficam os que caem dentro de uma UF pela malha do IBGE.
"""

from datetime import datetime, timedelta, timezone

from ..models import Sismo
from .geo import uf_do_ponto
from .http import ErroDeColeta, baixar

URL_FDSN = 'https://moho.iag.usp.br/fdsnws/event/1/query'

# Retângulo envolvente do Brasil.
RETANGULO = {'minlatitude': -34.0, 'maxlatitude': 5.5, 'minlongitude': -74.0, 'maxlongitude': -34.5}

DIAS_POR_COLETA = 60


def _numero(texto):
    try:
        return float(texto)
    except (TypeError, ValueError):
        return None


def interpretar(texto):
    """Texto FDSN (separado por '|') → dicts de Sismo, só eventos em território brasileiro."""
    linhas = [linha for linha in texto.splitlines() if linha.strip()]
    if not linhas or not linhas[0].startswith('#EventID'):
        raise ErroDeColeta('resposta da USP não é o formato FDSN esperado')
    cabecalho = [c.strip().lstrip('#') for c in linhas[0].split('|')]
    sismos = []
    for linha in linhas[1:]:
        valores = dict(zip(cabecalho, (v.strip() for v in linha.split('|'))))
        latitude = _numero(valores.get('Latitude'))
        longitude = _numero(valores.get('Longitude'))
        if latitude is None or longitude is None or not valores.get('EventID'):
            continue
        try:
            data_hora = datetime.fromisoformat(valores['Time']).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        uf = uf_do_ponto(latitude, longitude)
        if not uf:
            continue
        magnitude = _numero(valores.get('Magnitude'))
        profundidade = _numero(valores.get('Depth/km'))
        sismos.append({
            'id_evento': valores['EventID'],
            'data_hora': data_hora,
            'latitude': latitude,
            'longitude': longitude,
            'profundidade_km': round(profundidade, 1) if profundidade is not None else None,
            'magnitude': round(magnitude, 1) if magnitude is not None else None,
            'tipo_magnitude': valores.get('MagType', ''),
            'local': valores.get('EventLocationName', ''),
            'uf': uf,
        })
    return sismos


def coletar(agora=None):
    agora = agora or datetime.now(timezone.utc)
    inicio = (agora - timedelta(days=DIAS_POR_COLETA)).strftime('%Y-%m-%d')
    resposta = baixar(
        URL_FDSN,
        params={'format': 'text', 'starttime': inicio, 'orderby': 'time', **RETANGULO},
    )
    resposta.encoding = 'utf-8'
    sismos = interpretar(resposta.text)
    Sismo.objects.bulk_create(
        [Sismo(**sismo) for sismo in sismos],
        update_conflicts=True,
        unique_fields=['id_evento'],
        update_fields=['magnitude', 'tipo_magnitude', 'profundidade_km', 'local', 'latitude', 'longitude', 'uf'],
    )
    return len(sismos)
