"""Focos de calor do INPE Programa Queimadas (arquivos diários do Brasil).

Cada arquivo traz um dia UTC de todos os satélites que o INPE processa.
Os geoestacionários (GOES, MSG) redetectam o mesmo fogo a cada 10–15 min;
para não inflar as contagens, guardamos só os satélites de órbita polar.
"""

import csv
import io
from datetime import datetime, timedelta, timezone

from ..models import FocoQueimada
from .http import ColetaParcial, ErroDeColeta, baixar
from .ufs import sigla_por_nome, titulo

URL_DIARIO = (
    'https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/diario/Brasil/'
    'focos_diario_br_{data}.csv'
)

SATELITES_POLARES = {
    'NOAA-20', 'NOAA-21', 'NPP-375', 'NPP-375D',
    'AQUA_M-T', 'AQUA_M-M', 'TERRA_M-T', 'TERRA_M-M',
    'METOP-B', 'METOP-C',
}

CAMPOS_OBRIGATORIOS = {'id', 'lat', 'lon', 'data_hora_gmt', 'satelite'}


def interpretar(texto):
    """CSV diário do INPE → lista de dicts com os campos de FocoQueimada.

    Linhas malformadas são ignoradas; um texto que não é o CSV do INPE
    (página de erro) levanta ErroDeColeta.
    """
    leitor = csv.DictReader(io.StringIO(texto.lstrip('﻿')))
    if not CAMPOS_OBRIGATORIOS.issubset(leitor.fieldnames or []):
        raise ErroDeColeta('resposta do INPE não é o CSV de focos esperado')
    focos = []
    for linha in leitor:
        satelite = (linha.get('satelite') or '').strip().upper()
        if satelite not in SATELITES_POLARES:
            continue
        try:
            latitude = float(linha['lat'])
            longitude = float(linha['lon'])
            data_hora = datetime.strptime(
                linha['data_hora_gmt'].strip(), '%Y-%m-%d %H:%M:%S'
            ).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        try:
            frp = float(linha.get('frp') or '')
        except ValueError:
            frp = None
        focos.append({
            'id_inpe': linha['id'].strip(),
            'data_hora': data_hora,
            'latitude': latitude,
            'longitude': longitude,
            'satelite': satelite,
            'municipio': titulo(linha.get('municipio')),
            'uf': sigla_por_nome(linha.get('estado')),
            'bioma': (linha.get('bioma') or '').strip(),
            'frp': frp,
        })
    return focos


def datas_dos_arquivos(agora, dias=1):
    """Os `dias` anteriores e hoje (UTC), do mais antigo ao mais recente, no formato
    do nome do arquivo. Com dias=1 (ontem e hoje) cobrem as últimas 24 h."""
    return [(agora - timedelta(days=n)).strftime('%Y%m%d') for n in range(dias, -1, -1)]


def coletar(agora=None, dias=1):
    """Baixa os arquivos diários dos últimos `dias` e grava/atualiza os focos. Devolve o total.

    O INPE mantém os arquivos diários de cerca de 30 dias; dias=30 preenche o
    histórico de um banco novo.
    """
    agora = agora or datetime.now(timezone.utc)
    datas = datas_dos_arquivos(agora, dias)
    total, falhas = 0, []
    for data in datas:
        try:
            resposta = baixar(URL_DIARIO.format(data=data))
            resposta.encoding = 'utf-8'
            focos = interpretar(resposta.text)
        except ErroDeColeta as erro:
            # Logo após 00:00 UTC o arquivo do dia ainda não existe.
            falhas.append(f'{data}: {erro}')
            continue
        # Grava arquivo a arquivo: 30 dias de focos não precisam caber na memória.
        FocoQueimada.objects.bulk_create(
            [FocoQueimada(**foco) for foco in focos],
            batch_size=2000,
            update_conflicts=True,
            unique_fields=['id_inpe'],
            update_fields=['frp', 'municipio', 'uf', 'bioma'],
        )
        total += len(focos)
    if len(falhas) == len(datas):
        raise ErroDeColeta('; '.join(falhas))
    if falhas and dias > 1:
        raise ColetaParcial(total, falhas)
    return total
