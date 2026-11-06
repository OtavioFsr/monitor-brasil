"""Unidades da Federação: código IBGE, sigla e nome."""

import unicodedata

UFS = [
    ('11', 'RO', 'Rondônia'),
    ('12', 'AC', 'Acre'),
    ('13', 'AM', 'Amazonas'),
    ('14', 'RR', 'Roraima'),
    ('15', 'PA', 'Pará'),
    ('16', 'AP', 'Amapá'),
    ('17', 'TO', 'Tocantins'),
    ('21', 'MA', 'Maranhão'),
    ('22', 'PI', 'Piauí'),
    ('23', 'CE', 'Ceará'),
    ('24', 'RN', 'Rio Grande do Norte'),
    ('25', 'PB', 'Paraíba'),
    ('26', 'PE', 'Pernambuco'),
    ('27', 'AL', 'Alagoas'),
    ('28', 'SE', 'Sergipe'),
    ('29', 'BA', 'Bahia'),
    ('31', 'MG', 'Minas Gerais'),
    ('32', 'ES', 'Espírito Santo'),
    ('33', 'RJ', 'Rio de Janeiro'),
    ('35', 'SP', 'São Paulo'),
    ('41', 'PR', 'Paraná'),
    ('42', 'SC', 'Santa Catarina'),
    ('43', 'RS', 'Rio Grande do Sul'),
    ('50', 'MS', 'Mato Grosso do Sul'),
    ('51', 'MT', 'Mato Grosso'),
    ('52', 'GO', 'Goiás'),
    ('53', 'DF', 'Distrito Federal'),
]

SIGLA_POR_CODIGO = {codigo: sigla for codigo, sigla, _ in UFS}
NOME_POR_SIGLA = {sigla: nome for _, sigla, nome in UFS}
SIGLAS = set(NOME_POR_SIGLA)


def normalizar(texto):
    """'São Paulo' → 'SAO PAULO': sem acentos, maiúsculas, espaços simples."""
    sem_acento = unicodedata.normalize('NFKD', str(texto or ''))
    sem_acento = ''.join(c for c in sem_acento if not unicodedata.combining(c))
    return ' '.join(sem_acento.upper().split())


_SIGLA_POR_NOME = {normalizar(nome): sigla for _, sigla, nome in UFS}


def sigla_por_nome(nome):
    """Nome do estado em qualquer caixa/acentuação ('PARÁ', 'para') → 'PA', ou ''."""
    return _SIGLA_POR_NOME.get(normalizar(nome), '')


def titulo(texto):
    """'SÃO JOSÉ DOS CAMPOS' → 'São José dos Campos'."""
    minusculas = {'da', 'das', 'de', 'do', 'dos', 'e'}
    palavras = str(texto or '').lower().split()
    return ' '.join(
        p if i > 0 and p in minusculas else p[:1].upper() + p[1:]
        for i, p in enumerate(palavras)
    )
