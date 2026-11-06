"""Arboviroses nas cidades em foco: InfoDengue (Fiocruz e FGV), dados abertos.

Para cada cidade e doença (dengue e chikungunya), o InfoDengue publica por semana
epidemiológica os casos notificados, os casos estimados (corrigindo o atraso de
notificação), a incidência por 100 mil habitantes e um nível de alerta de 1 a 4.
Coletamos as últimas semanas (o InfoDengue revisa as recentes) e gravamos no banco.
"""

from datetime import date, datetime, timedelta, timezone

from ..cidades import CIDADES
from ..models import BoletimSaude
from .http import ColetaParcial, ErroDeColeta, baixar

URL_ALERTA = 'https://info.dengue.mat.br/api/alertcity'
# O InfoDengue também aceita 'zika', mas hoje devolve lista vazia para ela.
DOENCAS = {'dengue': 'Dengue', 'chikungunya': 'Chikungunya'}
SEMANAS = 10


def interpretar(registros, cidade, doenca):
    """Lista JSON do InfoDengue → dicts com os campos de BoletimSaude."""
    boletins = []
    for r in registros:
        try:
            semana = int(r['SE'])
            inicio = datetime.fromtimestamp(int(r['data_iniSE']) / 1000, tz=timezone.utc).date()
            nivel = int(r['nivel'])
        except (KeyError, TypeError, ValueError):
            continue
        if not 1 <= nivel <= 4:
            continue

        def numero(chave):
            try:
                return float(r[chave]) if r.get(chave) is not None else None
            except (TypeError, ValueError):
                return None

        casos = numero('casos')
        boletins.append({
            'cidade': cidade, 'doenca': doenca, 'semana': semana, 'inicio_semana': inicio, 'nivel': nivel,
            'casos': int(casos) if casos is not None else None,
            'casos_estimados': numero('casos_est'), 'incidencia': numero('p_inc100k'), 'rt': numero('Rt'),
        })
    return boletins


def coletar(hoje=None):
    """Baixa as últimas semanas de cada cidade e doença e grava/atualiza. Devolve quantos boletins gravou."""
    hoje = hoje or date.today()
    inicio = hoje - timedelta(weeks=SEMANAS)
    total, falhas = 0, []
    for cidade in CIDADES:
        for doenca in DOENCAS:
            try:
                resposta = baixar(URL_ALERTA, params={
                    'geocode': cidade['ibge'], 'disease': doenca, 'format': 'json',
                    'ew_start': 1, 'ew_end': 53, 'ey_start': inicio.year, 'ey_end': hoje.year,
                }, timeout=60)
                boletins = interpretar(resposta.json(), cidade['id'], doenca)
            except (ErroDeColeta, ValueError) as erro:
                falhas.append(f'{cidade["nome"]}/{doenca}: {str(erro)[:60]}')
                continue
            for b in boletins:
                if b['inicio_semana'] < inicio:
                    continue
                chave = {k: b.pop(k) for k in ('cidade', 'doenca', 'semana')}
                BoletimSaude.objects.update_or_create(**chave, defaults=b)
                total += 1
    if falhas and not total:
        raise ErroDeColeta('; '.join(falhas[:3]))
    if falhas:
        raise ColetaParcial(total, falhas)
    return total
