"""Cidades com enfoque no mapa: a sede e as nove representações regionais do Banco Central.

Endereços: "Endereços do Banco Central do Brasil", Relatório da Administração 2015
(https://www.bcb.gov.br/pre/surel/reladmbc/2015/msc/enderecos-bcb.pdf); os de Recife e
São Paulo aparecem iguais no Boletim Regional de 2023. Coordenadas: o prédio do
Banco Central no OpenStreetMap (Nominatim), conferido contra o endereço oficial;
Belém e Recife pelo número do endereço, onde o prédio não tem nome no OSM.
"""

# Ordem de norte a sul, como no filtro. `ibge`: código do município (cruza com os avisos do INMET e o InfoDengue).
CIDADES = [
    {
        'id': 'belem', 'ibge': '1501402', 'nome': 'Belém', 'uf': 'PA',
        'bc': {'unidade': 'Representação regional', 'lat': -1.44885, 'lon': -48.49880,
               'endereco': 'Boulevard Castilhos França, 708 – Centro', 'cep': '66010-020'},
    },
    {
        'id': 'fortaleza', 'ibge': '2304400', 'nome': 'Fortaleza', 'uf': 'CE',
        'bc': {'unidade': 'Representação regional', 'lat': -3.73393, 'lon': -38.52248,
               'endereco': 'Avenida Heráclito Graça, 273 – Centro', 'cep': '60140-061'},
    },
    {
        'id': 'recife', 'ibge': '2611606', 'nome': 'Recife', 'uf': 'PE',
        'bc': {'unidade': 'Representação regional', 'lat': -8.05367, 'lon': -34.87649,
               'endereco': 'Rua da Aurora, 1.259 – Santo Amaro', 'cep': '50040-090'},
    },
    {
        'id': 'salvador', 'ibge': '2927408', 'nome': 'Salvador', 'uf': 'BA',
        'bc': {'unidade': 'Representação regional', 'lat': -12.99992, 'lon': -38.50544,
               'endereco': 'Avenida Anita Garibaldi, 1.211 – Ondina', 'cep': '40210-901'},
    },
    {
        'id': 'brasilia', 'ibge': '5300108', 'nome': 'Brasília', 'uf': 'DF',
        'bc': {'unidade': 'Sede', 'lat': -15.80326, 'lon': -47.88548,
               'endereco': 'SBS, Quadra 3, Bloco B – Edifício-Sede', 'cep': '70074-900'},
    },
    {
        'id': 'belo-horizonte', 'ibge': '3106200', 'nome': 'Belo Horizonte', 'uf': 'MG',
        'bc': {'unidade': 'Representação regional', 'lat': -19.93264, 'lon': -43.94914,
               'endereco': 'Avenida Álvares Cabral, 1.605 – Santo Agostinho', 'cep': '30170-001'},
    },
    {
        'id': 'rio-de-janeiro', 'ibge': '3304557', 'nome': 'Rio de Janeiro', 'uf': 'RJ',
        'bc': {'unidade': 'Representação regional', 'lat': -22.90195, 'lon': -43.18276,
               'endereco': 'Avenida Presidente Vargas, 730 – Centro', 'cep': '20071-900'},
    },
    {
        'id': 'sao-paulo', 'ibge': '3550308', 'nome': 'São Paulo', 'uf': 'SP',
        'bc': {'unidade': 'Representação regional', 'lat': -23.55983, 'lon': -46.65743,
               'endereco': 'Avenida Paulista, 1.804 – Bela Vista', 'cep': '01310-922'},
    },
    {
        'id': 'curitiba', 'ibge': '4106902', 'nome': 'Curitiba', 'uf': 'PR',
        'bc': {'unidade': 'Representação regional', 'lat': -25.42109, 'lon': -49.26879,
               'endereco': 'Avenida Cândido de Abreu, 344 – Centro Cívico', 'cep': '80530-914'},
    },
    {
        'id': 'porto-alegre', 'ibge': '4314902', 'nome': 'Porto Alegre', 'uf': 'RS',
        'bc': {'unidade': 'Representação regional', 'lat': -30.03056, 'lon': -51.23458,
               'endereco': 'Rua Sete de Setembro, 586 – Centro', 'cep': '90010-190'},
    },
]
