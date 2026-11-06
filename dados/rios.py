"""Nível de rios perto das regionais do BC (telemetria da ANA), comparado com o último ano.

Em Porto Alegre e Belém o Monitor já acompanha o Guaíba e a maré (enchente.py). Nas outras
capitais, escolhemos a estação telemétrica da ANA mais útil perto do prédio do BC que tivesse
leituras recentes de nível (varredura de out/2026): muitas estações listadas como ativas não
devolvem dados, e em São Paulo, Brasília, Fortaleza e Salvador só sobram represas e usinas,
que não servem como sinal de cheia.

Como não há cotas oficiais de alerta acessíveis por API, o "normal" de cada estação é o seu
último ano de leituras (~35 mil, a cada 15 min): acima do percentil 95 é cheia que acontece
uns 18 dias por ano; acima do 99, menos de 4. O nível de agora é a mediana das 3 últimas
leituras, para que um pico isolado do sensor não vire alerta.
"""

from datetime import datetime, timedelta

from django.core.cache import cache

from .coletores.http import ErroDeColeta
from .enchente import HORARIO_DE_BRASILIA, leituras_da_ana

ESTACOES = {
    'recife': {
        'codigo': '39187800', 'rio': 'Rio Capibaribe', 'local': 'São Lourenço da Mata', 'lat': -7.9986, 'lon': -35.0392,
        'operadora': 'SGB', 'nota': 'rio acima do Recife: a cheia que chega à cidade passa antes por aqui',
    },
    'belo-horizonte': {
        'codigo': '41200430', 'rio': 'Rio das Velhas', 'local': 'Raposos', 'lat': -19.9631, 'lon': -43.8067,
        'operadora': 'SGB', 'nota': 'o rio que recebe o Arrudas e o Onça, a leste de BH (a estação do Arrudas está parada)',
    },
    'rio-de-janeiro': {
        'codigo': '59305071', 'rio': 'Rio Tijuca', 'local': 'Capela Mayrink', 'lat': -22.9568, 'lon': -43.2775,
        'operadora': 'INEA',
        'nota': 'no alto da Floresta da Tijuca: sobe rápido com chuva forte no maciço, antes de a água descer para a '
                'Tijuca e o Maracanã',
    },
    'curitiba': {
        'codigo': '65006090', 'rio': 'Rio Iguaçu', 'local': 'Comporta do Canal Água Limpa', 'lat': -25.4857, 'lon': -49.1813,
        'operadora': 'IAT-PR', 'nota': 'o Iguaçu na divisa de Curitiba com São José dos Pinhais',
    },
}
PERCENTIS = (50, 95, 99)
DIAS_DE_REFERENCIA = 365
DIAS_DA_SERIE = 7
HORAS_DA_TENDENCIA = 6
ATRASO_MAXIMO = timedelta(hours=6)
CACHE_REFERENCIA = 24 * 60 * 60
CACHE_RECENTE = 10 * 60


def percentil(ordenados, p):
    return ordenados[round(p / 100 * (len(ordenados) - 1))]


def referencia(leituras):
    """Leituras de um ano → {'p50', 'p95', 'p99', 'maximo', 'leituras'} em metros."""
    niveis = sorted(n for _, n in leituras)
    if len(niveis) < 1000:
        raise ErroDeColeta(f'só {len(niveis)} leituras no último ano: pouco para saber o que é normal')
    return {
        **{f'p{p}': round(percentil(niveis, p), 2) for p in PERCENTIS},
        'maximo': round(niveis[-1], 2),
        'leituras': len(niveis),
    }


def resumir(leituras, ref, agora):
    """Leituras recentes (da mais nova à mais velha) e a referência do ano → situação do rio agora."""
    ultima = leituras[0][0]
    if agora - ultima > ATRASO_MAXIMO:
        raise ErroDeColeta(f'a última leitura é de {ultima:%d/%m %H:%M}')
    nivel = sorted(n for _, n in leituras[:3])[len(leituras[:3]) // 2]
    antes = [n for q, n in leituras if q <= ultima - timedelta(hours=HORAS_DA_TENDENCIA)][:3]
    variacao = round(nivel - sorted(antes)[len(antes) // 2], 2) if antes else None
    # Série de uma leitura por hora (a última de cada hora), da mais velha à mais nova.
    por_hora = {}
    for quando, metros in leituras:
        por_hora.setdefault(quando.replace(minute=0, second=0, microsecond=0), (quando, metros))
    serie = [{'quando': q.isoformat(), 'metros': m} for q, m in sorted(por_hora.values())]
    return {
        'atual': {'quando': ultima.isoformat(), 'metros': round(nivel, 2)},
        'variacao_6h': variacao,
        'referencia': ref,
        'serie': serie,
    }


def situacao_do_rio(cidade_id, agora=None):
    """Estação, nível agora, variação em 6 h, referência do último ano e série de 7 dias. Levanta ErroDeColeta."""
    estacao = ESTACOES[cidade_id]
    agora = agora or datetime.now(HORARIO_DE_BRASILIA)
    chave = f'rios:{estacao["codigo"]}'
    em_cache = cache.get(chave)
    if em_cache:
        return em_cache
    ref = cache.get(f'{chave}:referencia')
    if ref is None:
        ref = referencia(leituras_da_ana(estacao['codigo'], DIAS_DE_REFERENCIA, agora))
        cache.set(f'{chave}:referencia', ref, CACHE_REFERENCIA)
    resultado = {
        'cidade': cidade_id, 'estacao': estacao,
        **resumir(leituras_da_ana(estacao['codigo'], DIAS_DA_SERIE, agora), ref, agora),
    }
    cache.set(chave, resultado, CACHE_RECENTE)
    return resultado
