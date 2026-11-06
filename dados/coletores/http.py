"""Cliente HTTP das coletas: identifica o projeto e tenta de novo em falhas passageiras.

Os servidores públicos (TerraBrasilis, em especial) oscilam bastante, então
erros de rede e respostas 5xx ganham novas tentativas com espera crescente.
"""

import time

import requests
from django.conf import settings


class ErroDeColeta(Exception):
    """A fonte não respondeu de forma utilizável depois das tentativas."""


class ColetaParcial(ErroDeColeta):
    """Parte das fontes falhou, mas o que veio foi gravado."""

    def __init__(self, registros, falhas):
        super().__init__('parcial — ' + '; '.join(falhas))
        self.registros = registros


def baixar(url, *, params=None, cabecalhos=None, tentativas=3, espera=2.0, timeout=90):
    """GET que devolve a resposta (status 2xx) ou levanta ErroDeColeta.

    `cabecalhos` extras (ex.: Range, para baixar só o começo de um arquivo grande).
    """
    cabecalhos = {**(cabecalhos or {}), 'User-Agent': settings.COLETA_USER_AGENT}
    ultimo_erro = None
    for tentativa in range(tentativas):
        if tentativa:
            time.sleep(espera * 2 ** (tentativa - 1))
        try:
            resposta = requests.get(url, params=params, headers=cabecalhos, timeout=timeout)
        except requests.RequestException as erro:
            ultimo_erro = f'falha de rede: {erro.__class__.__name__}'
            continue
        if resposta.status_code >= 500:
            ultimo_erro = f'HTTP {resposta.status_code}'
            continue
        if resposta.status_code >= 400:
            raise ErroDeColeta(f'HTTP {resposta.status_code} em {url}')
        return resposta
    raise ErroDeColeta(f'{ultimo_erro} em {url} após {tentativas} tentativas')
