"""Normais climatológicas do INMET: média das temperaturas máximas de cada mês (°C).

Copiadas da planilha "Normal-Climatologica-TMAX.xlsx" (normais 1991-2020) em
https://portal.inmet.gov.br/normais, estação convencional da capital. A de 1991-2020 não tem
a estação do centro do Rio de Janeiro (só a do Alto da Boa Vista, na Floresta da Tijuca,
1 a 3 °C mais fresca), então o Rio usa a normal 1981-2010 da estação 83743.
"""

# cidade → (código e nome da estação do INMET, período, máximas médias de janeiro a dezembro)
TEMPERATURA_MAXIMA = {
    'belem': ('82191 Belém', '1991-2020', (31.4, 31.0, 31.1, 31.5, 32.2, 32.5, 32.6, 33.0, 33.2, 33.1, 33.1, 32.5)),
    'fortaleza': ('82397 Fortaleza', '1991-2020', (31.2, 31.1, 30.8, 30.6, 30.8, 30.5, 30.6, 31.1, 31.4, 31.6, 31.7, 31.7)),
    'recife': ('82900 Recife (Curado)', '1991-2020', (30.8, 31.0, 30.9, 30.4, 29.6, 28.5, 27.9, 27.9, 28.6, 29.6, 30.4, 30.8)),
    'salvador': ('83229 Salvador (Ondina)', '1991-2020', (31.0, 31.1, 30.9, 29.6, 28.1, 27.1, 26.6, 26.7, 27.7, 29.1, 29.8, 30.6)),
    'brasilia': ('83377 Brasília', '1991-2020', (26.9, 27.2, 27.0, 26.8, 26.0, 25.3, 25.6, 27.4, 29.1, 29.0, 27.0, 26.8)),
    'belo-horizonte': ('83587 Belo Horizonte', '1991-2020', (28.7, 29.1, 28.4, 27.6, 25.7, 24.9, 24.9, 26.3, 27.9, 28.7, 27.7, 28.2)),
    'rio-de-janeiro': ('83743 Rio de Janeiro', '1981-2010', (31.5, 32.2, 31.2, 30.0, 27.8, 26.7, 26.4, 27.0, 26.7, 28.2, 29.4, 30.3)),
    'sao-paulo': ('83781 São Paulo (Mirante de Santana)', '1991-2020', (28.6, 29.0, 28.0, 26.6, 23.4, 22.9, 22.9, 24.5, 25.2, 26.5, 26.9, 28.3)),
    'curitiba': ('83842 Curitiba', '1991-2020', (27.1, 27.2, 26.1, 24.4, 21.1, 20.3, 20.1, 21.9, 22.3, 23.7, 25.0, 26.7)),
    'porto-alegre': ('83967 Porto Alegre', '1991-2020', (31.0, 30.5, 29.2, 26.4, 22.6, 20.3, 19.7, 21.8, 22.8, 25.2, 27.7, 30.0)),
}


def maxima_normal(cidade_id, mes):
    """Média das máximas do mês (1 a 12) na estação do INMET da capital, em °C."""
    return TEMPERATURA_MAXIMA[cidade_id][2][mes - 1]
