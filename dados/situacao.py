"""Situação das regionais do Banco Central: indicadores de ameaça em volta de cada prédio.

Para cada regional (dados/cidades.py) calcula, com o que o Monitor já coleta:
avisos do INMET e alertas do CEMADEN em vigor no município, tempestade (topo de nuvem mais frio por perto,
GOES-19), raios por perto (GLM do GOES-19), chuva das últimas 24 h (MERGE), chuva e calor previstos para as
próximas 72 h e vento previsto para a hora (Eta), focos de
queimada próximos, sismos recentes, dengue e chikungunya (InfoDengue), o nível do rio monitorado
perto do prédio (Recife, BH, Rio e Curitiba) e, em Porto Alegre e Belém, o nível do Guaíba e a maré. Cada indicador vira verde, amarelo ou vermelho
pelos LIMIARES abaixo, e a regional fica com o pior deles.

Cada avaliação é gravada (AvaliacaoSituacao). Indicador fora do verde abre um AlertaRegional,
que acompanha o pico e é fechado quando o indicador volta ao verde: é o histórico de quando
cada regional esteve sob ameaça. Indicador indisponível (fonte fora do ar ou dado velho) não
abre nem fecha alertas.
"""

import math
from datetime import datetime, time, timedelta, timezone

from django.core.cache import cache
from django.db.models import Max

from . import avisos, cemaden, enchente, normais, previsao, raios, rios, satelite, vento
from .chuva import Acumulado
from .cidades import CIDADES
from .coletores.http import ErroDeColeta
from .coletores.saude import DOENCAS
from .models import AlertaRegional, AvaliacaoSituacao, BoletimSaude, Coleta, FocoQueimada, PrevisaoTemperatura, Sismo

VERDE, AMARELO, VERMELHO, INDISPONIVEL = 'verde', 'amarelo', 'vermelho', 'indisponivel'
GRAVIDADE = {INDISPONIVEL: -1, VERDE: 0, AMARELO: 1, VERMELHO: 2}

# Limiares de cada indicador. Referências: topos de nuvem abaixo de -60 °C costumam ser
# tempestade; os avisos do INMET tratam 30–50 mm/dia como perigo potencial e acima de 50 mm
# como perigo; cotas do Guaíba no Cais Mauá da Defesa Civil do RS.
LIMIARES = {
    'tempestade': {'raio_km': 20, 'amarelo_c': -50, 'vermelho_c': -60},
    # Raios (áreas de 0,1° com raio, distância até o centro da área): amarelo com raio a até 20 km
    # na última hora; vermelho com raio a até 10 km nos 15 min antes da última imagem do GLM.
    'raios': {'raio_km': 20, 'janela_min': 60, 'perto_km': 10, 'recente_min': 15, 'atraso_maximo_min': 30},
    'chuva': {'amarelo_mm': 30, 'vermelho_mm': 50},
    # Chuva prevista: a pior janela de 24 h nas próximas 72 h, com as faixas dos avisos de chuvas
    # intensas do INMET (50–100 mm/dia é "perigo", acima de 100 mm "grande perigo"). Mais altas
    # que as da chuva observada porque é previsão de um modelo de 40 km.
    'chuva_prevista': {'amarelo_mm': 50, 'vermelho_mm': 100},
    # Calor: máxima prevista de cada 24 h contra a normal do mês do INMET (normais.py). O INMET chama
    # de onda de calor máximas 5 °C acima da normal por 2 dias ou mais. Sem limiar absoluto no amarelo:
    # o Eta tem viés de alguns graus em algumas capitais, e a normal é que dá a escala de cada uma.
    'calor': {'acima_da_normal_c': 5, 'dias_onda': 2, 'vermelho_c': 40},
    'vento': {'amarelo_kmh': 30, 'vermelho_kmh': 50},
    'queimadas': {'raio_km': 15, 'perto_km': 3, 'muitos': 10},
    'sismos': {'dias': 7, 'amarelo_m': 2.5, 'amarelo_km': 100, 'vermelho_m': 4.0, 'vermelho_km': 200},
    'guaiba': {'amarelo_m': 2.55, 'vermelho_m': 3.00},
    # Rio perto do prédio (rios.py): percentis do último ano de leituras da própria estação.
    'rio': {'amarelo_percentil': 95, 'vermelho_percentil': 99},
    'mare_belem': {'chuva_mm': 30},
    # Aviso do INMET: Perigo Potencial → amarelo; Perigo e Grande Perigo → vermelho (avisos.NIVEL_DA_SEVERIDADE).
    # Alerta do CEMADEN: Moderado → amarelo; Alto e Muito Alto → vermelho (cemaden.NIVEL_DO_ALERTA).
    # InfoDengue: nível 3 (transmissão sustentada) → amarelo; 4 (acima do limiar epidêmico) → vermelho.
    'saude': {'amarelo_nivel': 3, 'vermelho_nivel': 4},
}
NOMES = {
    'aviso_inmet': 'Aviso do INMET', 'cemaden': 'Alerta do CEMADEN', 'saude': 'Dengue e chikungunya',
    'tempestade': 'Tempestade', 'raios': 'Raios', 'chuva': 'Chuva 24 h', 'chuva_prevista': 'Chuva prevista', 'calor': 'Calor', 'vento': 'Vento', 'queimadas': 'Queimadas',
    'sismos': 'Sismos', 'rio': 'Rio', 'guaiba': 'Guaíba', 'mare_belem': 'Maré e chuva',
}
# Dados que vêm do banco ficam "indisponíveis" se a última coleta bem-sucedida for mais velha que isto.
VALIDADE_COLETA = {'queimadas': timedelta(hours=12), 'sismos': timedelta(days=2), 'saude': timedelta(days=10)}
IDADE_MAXIMA_AVALIACAO = timedelta(minutes=10)
GUARDAR_AVALIACOES = timedelta(days=7)
HORARIO_DE_BRASILIA = enchente.HORARIO_DE_BRASILIA


def _num(valor, casas=0):
    texto = f'{valor:,.{casas}f}'
    return texto.replace(',', '_').replace('.', ',').replace('_', '.')


def _distancia_km(lat1, lon1, lat2, lon2):
    return satelite._distancia_km(lat1, lon1, lat2, lon2)


def _indicador(chave, nivel, texto, dados_de=None):
    return {
        'id': chave, 'nome': NOMES[chave], 'nivel': nivel, 'texto': texto,
        'dados_de': dados_de.isoformat() if dados_de else None,
    }


def _classificar(valor, amarelo, vermelho, menor_e_pior=False):
    if menor_e_pior:
        valor, amarelo, vermelho = -valor, -amarelo, -vermelho
    return VERMELHO if valor >= vermelho else AMARELO if valor >= amarelo else VERDE


def _coleta_recente(fonte, agora):
    ultima = Coleta.objects.filter(fonte=fonte, sucesso=True).aggregate(m=Max('concluida_em'))['m']
    return ultima if ultima and agora - ultima <= VALIDADE_COLETA[fonte] else None


# ---- Indicadores -----------------------------------------------------------

def tempestade(cidade, agora):
    lim = LIMIARES['tempestade']
    bc = cidade['bc']
    r = satelite.nuvem_mais_fria_perto(bc['lat'], bc['lon'], lim['raio_km'])
    quando = satelite.como_datetime(r['horario'])
    if r['celsius'] is None:
        return _indicador('tempestade', VERDE, f'Sem nuvens altas a até {lim["raio_km"]} km', quando)
    nivel = _classificar(r['celsius'], lim['amarelo_c'], lim['vermelho_c'], menor_e_pior=True)
    frio = 'abaixo de -69' if r['celsius'] <= -69 else _num(r['celsius'])
    return _indicador('tempestade', nivel, f'Topo de nuvem a {frio} °C, a {_num(r["distancia_km"])} km', quando)


def raios_perto(cidade, dados, agora):
    """Raios em volta do prédio do BC. `dados`: raios.recentes() da janela do limiar."""
    lim = LIMIARES['raios']
    ultimo = datetime.fromisoformat(dados['ultimo'])
    atraso = round((agora - ultimo) / timedelta(minutes=1))
    if atraso > lim['atraso_maximo_min']:
        return _indicador('raios', INDISPONIVEL, f'Última imagem do GLM tem {atraso} min', ultimo)
    bc = cidade['bc']
    perto = []
    for lat, lon, _, idade in dados['celulas']:
        distancia = _distancia_km(bc['lat'], bc['lon'], lat, lon)
        if distancia <= lim['raio_km']:
            perto.append((idade, distancia))
    if not perto:
        return _indicador('raios', VERDE, f'Sem raios a até {lim["raio_km"]} km na última hora', ultimo)
    idade, distancia = min(perto)  # o mais recente; empate: o mais perto
    agora_perto = any(i <= lim['recente_min'] and d <= lim['perto_km'] for i, d in perto)
    texto = f'Raio a {_num(distancia)} km há {idade + max(0, atraso)} min'
    if len(perto) > 1:
        texto += f' ({len(perto)} áreas com raios a até {lim["raio_km"]} km)'
    return _indicador('raios', VERMELHO if agora_perto else AMARELO, texto, ultimo)


def chuva(cidade, acumulado, agora, lat=None, lon=None):
    lim = LIMIARES['chuva']
    if acumulado.vazio or (agora.date() - acumulado.fim).days > 2:
        return _indicador('chuva', INDISPONIVEL, 'Sem dados de chuva recentes')
    mm = acumulado.no_ponto(lat if lat is not None else cidade['bc']['lat'], lon if lon is not None else cidade['bc']['lon'])
    quando = datetime.combine(acumulado.fim, time(12), tzinfo=timezone.utc)
    if mm is None:
        return _indicador('chuva', INDISPONIVEL, 'Sem dado de chuva no ponto', quando)
    nivel = _classificar(mm, lim['amarelo_mm'], lim['vermelho_mm'])
    return _indicador('chuva', nivel, f'{_num(mm, 1 if mm < 10 else 0)} mm nas 24 h até 9h de {acumulado.fim:%d/%m}', quando)


def _vento_no_ponto(grade, lat, lon):
    y = (lat - grade['lat_sul']) / grade['passo']
    x = (lon - grade['lon_oeste']) / grade['passo']
    if not (0 <= y <= grade['linhas'] - 1 and 0 <= x <= grade['colunas'] - 1):
        return None
    i, j = min(int(y), grade['linhas'] - 2), min(int(x), grade['colunas'] - 2)
    fy, fx = y - i, x - j

    def bilinear(c):
        return (c[i][j] * (1 - fx) * (1 - fy) + c[i][j + 1] * fx * (1 - fy)
                + c[i + 1][j] * (1 - fx) * fy + c[i + 1][j + 1] * fx * fy)

    return bilinear(grade['u']), bilinear(grade['v'])


DIAS_DA_SEMANA = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom']


def chuva_prevista(cidade, proximas, agora):
    """Chuva prevista pelo Eta nas próximas 72 h no prédio do BC. `proximas`: previsao.proximas_horas().
    Além do texto, o indicador leva `dias_mm`: os mm de cada 24 h (0–24, 24–48 e 48–72 h)."""
    import numpy as np

    lim = LIMIARES['chuva_prevista']
    if proximas is None:
        return _indicador('chuva_prevista', INDISPONIVEL, 'Sem previsão recente do Eta')
    rodada, valores, inicio = proximas
    serie = previsao.serie_no_ponto(rodada, valores, cidade['bc']['lat'], cidade['bc']['lon'])
    if serie is None:
        return _indicador('chuva_prevista', INDISPONIVEL, 'Fora da grade do modelo', rodada.rodada)
    horas = serie[inicio:inicio + previsao.HORAS]
    dias = [float(horas[k:k + 24].sum()) for k in (0, 24, 48)]
    janelas = np.convolve(horas, np.ones(24), mode='valid')  # todas as janelas de 24 h
    pior = int(janelas.argmax())
    maximo = float(janelas[pior])
    nivel = _classificar(maximo, lim['amarelo_mm'], lim['vermelho_mm'])
    mm = lambda valor: _num(valor, 1 if valor < 10 else 0)  # noqa: E731
    texto = f'{mm(sum(dias))} mm nas próximas 72 h'
    if nivel != VERDE:
        fim = (rodada.primeira_hora + timedelta(hours=inicio + pior + 23)).astimezone(HORARIO_DE_BRASILIA)
        texto += f', até {mm(maximo)} mm em 24 h (até {DIAS_DA_SEMANA[fim.weekday()]} {fim:%d/%m %H}h)'
    indicador = _indicador('chuva_prevista', nivel, texto, rodada.rodada)
    indicador['dias_mm'] = [round(d, 1) for d in dias]
    return indicador


MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro',
         'novembro', 'dezembro']


def calor_previsto(cidade, proximas, agora):
    """Máxima prevista pelo Eta em cada 24 h das próximas 72 h no prédio do BC, contra a normal do mês.
    `proximas`: previsao.proximas_horas(modelo=PrevisaoTemperatura). Além do texto, o indicador leva
    `dias_c` (máxima de 0–24, 24–48 e 48–72 h) e `normais_c` (a normal do mês de cada um)."""
    lim = LIMIARES['calor']
    if proximas is None:
        return _indicador('calor', INDISPONIVEL, 'Sem previsão recente do Eta')
    rodada, valores, inicio = proximas
    serie = previsao.serie_no_ponto(rodada, valores, cidade['bc']['lat'], cidade['bc']['lon'])
    if serie is None:
        return _indicador('calor', INDISPONIVEL, 'Fora da grade do modelo', rodada.rodada)
    horas = serie[inicio:inicio + previsao.HORAS]
    dias = []  # (máxima, normal do mês em que cai o meio das 24 h, mês)
    for k in (0, 24, 48):
        meio = (rodada.primeira_hora + timedelta(hours=inicio + k + 11)).astimezone(HORARIO_DE_BRASILIA)
        dias.append((float(horas[k:k + 24].max()), normais.maxima_normal(cidade['id'], meio.month), meio.month))
    quentes = [d for d in dias if d[0] >= d[1] + lim['acima_da_normal_c']]
    maxima, normal, mes = max(dias, key=lambda d: d[0] - d[1])  # o dia mais acima da normal
    if len(quentes) >= lim['dias_onda'] or max(d[0] for d in dias) >= lim['vermelho_c']:
        nivel = VERMELHO
    elif quentes:
        nivel = AMARELO
    else:
        nivel = VERDE
    texto = f'Máxima de {_num(maxima)} °C (normal de {MESES[mes - 1]}: {_num(normal, 1)} °C)'
    if len(quentes) >= lim['dias_onda']:
        texto += f'; {len(quentes)} dias {lim["acima_da_normal_c"]} °C ou mais acima da normal: onda de calor'
    elif quentes:
        texto += f'; {_num(maxima - normal)} °C acima da normal'
    indicador = _indicador('calor', nivel, texto, rodada.rodada)
    indicador['dias_c'] = [round(d[0], 1) for d in dias]
    indicador['normais_c'] = [d[1] for d in dias]
    return indicador


def vento_previsto(cidade, grade, agora):
    lim = LIMIARES['vento']
    uv = _vento_no_ponto(grade, cidade['bc']['lat'], cidade['bc']['lon'])
    quando = datetime.fromisoformat(grade['valido_em'])
    if uv is None:
        return _indicador('vento', INDISPONIVEL, 'Fora da grade do modelo', quando)
    u, v = uv
    kmh = math.hypot(u, v) * 3.6
    de = (math.degrees(math.atan2(-u, -v)) + 360) % 360
    cardeal = ['N', 'NE', 'L', 'SE', 'S', 'SO', 'O', 'NO'][round(de / 45) % 8]
    nivel = _classificar(kmh, lim['amarelo_kmh'], lim['vermelho_kmh'])
    return _indicador('vento', nivel, f'{_num(kmh)} km/h vindo de {cardeal} (previsão do Eta)', quando)


def queimadas(cidade, agora):
    lim = LIMIARES['queimadas']
    coleta = _coleta_recente('queimadas', agora)
    if not coleta:
        return _indicador('queimadas', INDISPONIVEL, 'Coleta de focos desatualizada')
    lat, lon = cidade['bc']['lat'], cidade['bc']['lon']
    grau = lim['raio_km'] / 111 + 0.05
    focos = FocoQueimada.objects.filter(
        data_hora__gte=agora - timedelta(hours=24),
        latitude__range=(lat - grau, lat + grau), longitude__range=(lon - grau / math.cos(math.radians(lat)), lon + grau / math.cos(math.radians(lat))),
    ).values_list('latitude', 'longitude')
    distancias = sorted(d for d in (_distancia_km(lat, lon, a, b) for a, b in focos) if d <= lim['raio_km'])
    if not distancias:
        return _indicador('queimadas', VERDE, f'Nenhum foco a até {lim["raio_km"]} km nas últimas 24 h', coleta)
    nivel = VERMELHO if distancias[0] <= lim['perto_km'] or len(distancias) >= lim['muitos'] else AMARELO
    plural = 'foco' if len(distancias) == 1 else 'focos'
    return _indicador('queimadas', nivel, f'{len(distancias)} {plural} a até {lim["raio_km"]} km '
                                          f'(o mais perto a {_num(distancias[0], 1)} km) nas últimas 24 h', coleta)


def sismos(cidade, agora):
    lim = LIMIARES['sismos']
    coleta = _coleta_recente('sismos', agora)
    if not coleta:
        return _indicador('sismos', INDISPONIVEL, 'Coleta de sismos desatualizada')
    lat, lon = cidade['bc']['lat'], cidade['bc']['lon']
    pior = None
    for s in Sismo.objects.filter(data_hora__gte=agora - timedelta(days=lim['dias']), magnitude__isnull=False):
        d = _distancia_km(lat, lon, s.latitude, s.longitude)
        if s.magnitude >= lim['vermelho_m'] and d <= lim['vermelho_km']:
            nivel = VERMELHO
        elif s.magnitude >= lim['amarelo_m'] and d <= lim['amarelo_km']:
            nivel = AMARELO
        else:
            continue
        if pior is None or (GRAVIDADE[nivel], s.magnitude) > (GRAVIDADE[pior[0]], pior[1].magnitude):
            pior = (nivel, s, d)
    if pior is None:
        return _indicador('sismos', VERDE, f'Nenhum sismo relevante por perto em {lim["dias"]} dias', coleta)
    nivel, s, d = pior
    return _indicador('sismos', nivel, f'Magnitude {_num(s.magnitude, 1)} a {_num(d)} km em {s.data_hora.astimezone(HORARIO_DE_BRASILIA):%d/%m}', coleta)


def guaiba(agora):
    lim = LIMIARES['guaiba']
    atual = enchente.nivel_do_guaiba()['atual']
    quando = datetime.fromisoformat(atual['quando'])
    if agora - quando > timedelta(hours=6):
        return _indicador('guaiba', INDISPONIVEL, 'Leitura do Cais Mauá desatualizada', quando)
    nivel = _classificar(atual['metros'], lim['amarelo_m'], lim['vermelho_m'])
    return _indicador('guaiba', nivel, f'{_num(atual["metros"], 2)} m no Cais Mauá (alerta {_num(lim["amarelo_m"], 2)} m, '
                                       f'inundação {_num(lim["vermelho_m"], 2)} m)', quando)


def rio_perto(cidade, agora):
    """Nível do rio monitorado perto do prédio, comparado com o último ano da estação."""
    lim = LIMIARES['rio']
    r = rios.situacao_do_rio(cidade['id'], agora.astimezone(HORARIO_DE_BRASILIA))
    nivel, ref = r['atual']['metros'], r['referencia']
    quando = datetime.fromisoformat(r['atual']['quando'])
    situacao = _classificar(nivel, ref[f'p{lim["amarelo_percentil"]}'], ref[f'p{lim["vermelho_percentil"]}'])
    if situacao == VERMELHO:
        comparacao = f'acima de {lim["vermelho_percentil"]}% das leituras do último ano'
    elif situacao == AMARELO:
        comparacao = f'acima de {lim["amarelo_percentil"]}% das leituras do último ano'
    else:
        comparacao = f'normal (cheia a partir de {_num(ref["p95"], 2)} m)'
    texto = f'{r["estacao"]["rio"]}: {_num(nivel, 2)} m, {comparacao}'
    if r['variacao_6h'] is not None and abs(r['variacao_6h']) >= 0.05:
        texto += f' · {"subiu" if r["variacao_6h"] > 0 else "desceu"} {_num(abs(r["variacao_6h"]) * 100)} cm em 6 h'
    return _indicador('rio', situacao, texto, quando)


def mare_belem(acumulado, agora):
    mare = enchente.mare_de_belem()
    quando = datetime.fromisoformat(mare['atual']['quando'])
    if agora - quando > timedelta(hours=6):
        return _indicador('mare_belem', INDISPONIVEL, 'Leitura da maré desatualizada', quando)
    p90 = mare['mares_altas']['p90']
    # A maior maré das últimas ~13 h (um ciclo): a leitura atual ou a última maré alta confirmada.
    recentes = [m['relativo'] for m in mare['mares_altas']['recentes']
                if agora - datetime.fromisoformat(m['quando']) <= timedelta(hours=13)]
    maior = max([mare['atual']['relativo'], *recentes])
    lat, lon = enchente.CENTRO_BELEM
    mm = None if acumulado.vazio else acumulado.no_ponto(lat, lon)
    alta = maior >= p90
    nivel = VERDE
    if alta:
        nivel = VERMELHO if mm is not None and mm >= LIMIARES['mare_belem']['chuva_mm'] else AMARELO
    sinal = lambda m: ('+' if m >= 0 else '-') + _num(abs(m), 2)  # noqa: E731
    texto = f'Maré alta de {sinal(maior)} m (as 10% maiores passam de {sinal(p90)} m)'
    if mm is not None:
        texto += f', chuva de {_num(mm, 0)} mm'
    return _indicador('mare_belem', nivel, texto, quando)


def aviso_inmet(cidade, lista, agora):
    def descricao(a):
        return f'{a["evento"]} ({a["severidade"].lower()})'

    do_municipio = [a for a in lista if cidade['ibge'] in a['geocodes']]
    vigentes = [a for a in do_municipio if avisos.em_vigor(a, agora)]
    if not vigentes:
        proximos = sorted((a for a in do_municipio if a['inicio'] > agora),
                          key=lambda a: (-avisos.ORDEM_DA_SEVERIDADE.get(a['severidade'], 0), a['inicio']))
        texto = 'Nenhum aviso em vigor'
        if proximos:
            texto += f'; próximo: {descricao(proximos[0])} em {proximos[0]["inicio"].astimezone(HORARIO_DE_BRASILIA):%d/%m}'
        return _indicador('aviso_inmet', VERDE, texto, agora)
    pior = max(vigentes, key=lambda a: avisos.ORDEM_DA_SEVERIDADE.get(a['severidade'], 0))
    nivel = avisos.NIVEL_DA_SEVERIDADE.get(pior['severidade'], AMARELO)
    texto = f'{descricao(pior)} até {pior["fim"].astimezone(HORARIO_DE_BRASILIA):%H:%M de %d/%m}'
    if len(vigentes) > 1:
        outros = ', '.join(a['evento'].lower() for a in vigentes if a is not pior)
        texto += f'; também {outros}'
    return _indicador('aviso_inmet', nivel, texto, agora)


def alerta_cemaden(cidade, lista, agora):
    do_municipio = [a for a in lista if a['codibge'] == cidade['ibge']]
    if not do_municipio:
        return _indicador('cemaden', VERDE, 'Nenhum alerta de risco geo-hidrológico em vigor', agora)
    pior = max(do_municipio, key=lambda a: cemaden.ORDEM_DO_NIVEL.get(a['nivel'], 0))
    nivel = cemaden.NIVEL_DO_ALERTA.get(pior['nivel'], AMARELO)
    texto = f'{pior["tipo"]} ({pior["nivel"].lower()})'
    if pior['criado_em']:
        texto += f' desde {pior["criado_em"].astimezone(HORARIO_DE_BRASILIA):%d/%m %H:%M}'
    if len(do_municipio) > 1:
        texto += f'; também {", ".join(a["tipo"].lower() for a in do_municipio if a is not pior)}'
    return _indicador('cemaden', nivel, texto, agora)


SITUACAO_DENGUE = {1: 'condições desfavoráveis', 2: 'clima favorável à transmissão',
                   3: 'transmissão sustentada', 4: 'acima do limiar epidêmico'}


def saude(cidade, agora):
    lim = LIMIARES['saude']
    coleta = _coleta_recente('saude', agora)
    if not coleta:
        return _indicador('saude', INDISPONIVEL, 'Coleta do InfoDengue desatualizada')
    ultimos = []
    for doenca, nome in DOENCAS.items():
        boletim = BoletimSaude.objects.filter(cidade=cidade['id'], doenca=doenca).order_by('-semana').first()
        if boletim:
            ultimos.append((nome, boletim))
    if not ultimos:
        return _indicador('saude', INDISPONIVEL, 'Sem boletins do InfoDengue', coleta)
    pior = max(b.nivel for _, b in ultimos)
    nivel = VERMELHO if pior >= lim['vermelho_nivel'] else AMARELO if pior >= lim['amarelo_nivel'] else VERDE
    semana = max(b.semana for _, b in ultimos) % 100
    destaque = [(n, b) for n, b in ultimos if b.nivel >= lim['amarelo_nivel']]
    if not destaque:
        return _indicador('saude', VERDE, f'Sem transmissão sustentada (semana {semana})', coleta)
    partes = [f'{n}: {SITUACAO_DENGUE[b.nivel]}' + (f', {_num(b.incidencia, 1)} casos/100 mil' if b.incidencia is not None else '')
              for n, b in destaque]
    return _indicador('saude', nivel, '; '.join(partes) + f' (semana {semana})', coleta)


# ---- Avaliação e alertas ---------------------------------------------------

def _ou_indisponivel(chave, funcao, *args):
    try:
        return funcao(*args)
    except ErroDeColeta as erro:
        return _indicador(chave, INDISPONIVEL, f'Fonte indisponível ({str(erro)[:80]})')


def avaliar(agora=None):
    """Indicadores de todas as regionais agora. Uma fonte fora do ar só deixa o indicador indisponível."""
    agora = agora or datetime.now(timezone.utc)
    acumulado = Acumulado(1)
    proximas = previsao.proximas_horas(agora)
    temperaturas = previsao.proximas_horas(agora, PrevisaoTemperatura)
    try:
        dados_raios = raios.recentes(LIMIARES['raios']['janela_min'])
    except ErroDeColeta as erro:
        dados_raios = erro
    try:
        grade_vento = vento.vento_agora()
    except ErroDeColeta as erro:
        grade_vento = erro
    try:
        lista_avisos = avisos.avisos_ativos()
    except ErroDeColeta as erro:
        lista_avisos = erro
    try:
        lista_cemaden = cemaden.alertas_ativos()
    except ErroDeColeta as erro:
        lista_cemaden = erro
    regionais = []
    for cidade in CIDADES:
        indicadores = [
            (_indicador('aviso_inmet', INDISPONIVEL, f'Fonte indisponível ({str(lista_avisos)[:80]})')
             if isinstance(lista_avisos, Exception) else aviso_inmet(cidade, lista_avisos, agora)),
            (_indicador('cemaden', INDISPONIVEL, f'Fonte indisponível ({str(lista_cemaden)[:80]})')
             if isinstance(lista_cemaden, Exception) else alerta_cemaden(cidade, lista_cemaden, agora)),
            _ou_indisponivel('tempestade', tempestade, cidade, agora),
            (_indicador('raios', INDISPONIVEL, f'Fonte indisponível ({str(dados_raios)[:80]})')
             if isinstance(dados_raios, Exception) else raios_perto(cidade, dados_raios, agora)),
            chuva(cidade, acumulado, agora),
            chuva_prevista(cidade, proximas, agora),
            calor_previsto(cidade, temperaturas, agora),
            (_indicador('vento', INDISPONIVEL, f'Fonte indisponível ({str(grade_vento)[:80]})')
             if isinstance(grade_vento, Exception) else vento_previsto(cidade, grade_vento, agora)),
            queimadas(cidade, agora),
            sismos(cidade, agora),
            saude(cidade, agora),
        ]
        if cidade['id'] in rios.ESTACOES:
            indicadores.append(_ou_indisponivel('rio', rio_perto, cidade, agora))
        if cidade['id'] == 'porto-alegre':
            indicadores.append(_ou_indisponivel('guaiba', guaiba, agora))
        if cidade['id'] == 'belem':
            indicadores.append(_ou_indisponivel('mare_belem', mare_belem, acumulado, agora))
        validos = [i['nivel'] for i in indicadores if i['nivel'] != INDISPONIVEL]
        regionais.append({
            'id': cidade['id'], 'nome': cidade['nome'], 'uf': cidade['uf'],
            'nivel': max(validos, key=GRAVIDADE.get) if validos else INDISPONIVEL,
            'indicadores': indicadores,
        })
    return regionais


def registrar_alertas(regionais, momento):
    """Abre, atualiza e fecha os alertas conforme a avaliação. Devolve quantos estão abertos."""
    for regional in regionais:
        for ind in regional['indicadores']:
            if ind['nivel'] == INDISPONIVEL:
                continue
            aberto = AlertaRegional.objects.filter(cidade=regional['id'], indicador=ind['id'], fim__isnull=True).first()
            if ind['nivel'] == VERDE:
                if aberto:
                    aberto.fim = momento
                    aberto.atualizado_em = momento
                    aberto.save()
                continue
            if aberto is None:
                AlertaRegional.objects.create(
                    cidade=regional['id'], indicador=ind['id'], nivel=ind['nivel'], nivel_maximo=ind['nivel'],
                    inicio=momento, atualizado_em=momento, texto=ind['texto'][:200], texto_pico=ind['texto'][:200],
                )
                continue
            aberto.nivel = ind['nivel']
            aberto.texto = ind['texto'][:200]
            aberto.atualizado_em = momento
            if GRAVIDADE[ind['nivel']] > GRAVIDADE[aberto.nivel_maximo]:
                aberto.nivel_maximo = ind['nivel']
                aberto.texto_pico = ind['texto'][:200]
            aberto.save()
    return AlertaRegional.objects.filter(fim__isnull=True).count()


def avaliar_e_registrar(agora=None):
    agora = agora or datetime.now(timezone.utc)
    regionais = avaliar(agora)
    avaliacao = AvaliacaoSituacao.objects.create(momento=agora, regionais=regionais)
    registrar_alertas(regionais, agora)
    AvaliacaoSituacao.objects.filter(momento__lt=agora - GUARDAR_AVALIACOES).delete()
    return avaliacao


def situacao_atual(agora=None):
    """A avaliação mais recente, refeita se tiver mais de 10 min (uma por vez)."""
    agora = agora or datetime.now(timezone.utc)
    ultima = AvaliacaoSituacao.objects.order_by('-momento').first()
    if ultima and agora - ultima.momento <= IDADE_MAXIMA_AVALIACAO:
        return ultima
    if not cache.add('situacao:avaliando', True, 120):  # outra requisição já está avaliando
        return ultima or avaliar_e_registrar(agora)
    try:
        return avaliar_e_registrar(agora)
    finally:
        cache.delete('situacao:avaliando')


def coletar():
    """Fonte 'situacao' do comando coletar: avalia as regionais e atualiza os alertas."""
    avaliar_e_registrar()
    return len(CIDADES)
