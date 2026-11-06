import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from dados import enchente
from dados.coletores.http import ErroDeColeta

TAMANHO_DESCRICAO = 420
CAMPOS = {  # coluna da tabela Setores → propriedade do GeoJSON
    'cd_setor': 'codigo', 'LOCAL': 'local', 'TIPOLO_G1': 'tipo', 'GRAU_RISCO': 'grau',
    'NUM_EDIF': 'edificacoes', 'NUM_PESS': 'pessoas', 'DATA_SETOR': 'data', 'DESCRICAO': 'descricao',
}


class Command(BaseCommand):
    help = ('Baixa a setorização de riscos geológicos de Belém (SGB/CPRM, revisita de 2021) e grava os '
            'setores de inundação, alagamento e erosão usados no mapa. Basta rodar uma vez (~116 MB).')

    def add_arguments(self, parser):
        parser.add_argument('--zip', type=Path, metavar='ARQUIVO',
                            help='Usar este sig_belem_pa_risco_revisita_2021.zip já baixado.')

    def handle(self, *args, zip=None, **options):
        try:
            with tempfile.TemporaryDirectory() as pasta:
                if not zip:
                    zip = Path(pasta) / 'sgb_belem.zip'
                    self.stdout.write('Baixando a setorização de risco de Belém do SGB (116 MB)...')
                    self._baixar(enchente.URL_SETORES_BELEM, zip)
                setores = self._ler_setores(zip, Path(pasta))
        except (ErroDeColeta, zipfile.BadZipFile, sqlite3.Error, StopIteration) as erro:
            raise CommandError(f'não consegui preparar os setores de Belém: {erro}') from erro
        enchente.PASTA.mkdir(parents=True, exist_ok=True)
        enchente.ARQUIVO_SETORES_BELEM.write_text(json.dumps(setores, ensure_ascii=False), encoding='utf-8')
        por_tipo = {}
        for f in setores['features']:
            tipo = f['properties']['tipo']
            setores_e_pessoas = por_tipo.setdefault(tipo, [0, 0])
            setores_e_pessoas[0] += 1
            setores_e_pessoas[1] += f['properties']['pessoas'] or 0
        resumo = ', '.join(f'{tipo}: {n} setores, {p} pessoas' for tipo, (n, p) in sorted(por_tipo.items()))
        self.stdout.write(self.style.SUCCESS(
            f'Pronto: {enchente.ARQUIVO_SETORES_BELEM.name} '
            f'({enchente.ARQUIVO_SETORES_BELEM.stat().st_size // 1024} KB). {resumo}.'
        ))

    def _baixar(self, url, destino):
        try:
            with requests.get(url, headers={'User-Agent': settings.COLETA_USER_AGENT}, stream=True, timeout=120) as resposta:
                if resposta.status_code >= 400:
                    raise ErroDeColeta(f'HTTP {resposta.status_code} em {url}')
                with open(destino, 'wb') as arquivo:
                    for pedaco in resposta.iter_content(chunk_size=1024 * 1024):
                        arquivo.write(pedaco)
        except requests.RequestException as erro:
            raise ErroDeColeta(f'falha de rede ao baixar {url}: {erro.__class__.__name__}') from erro

    def _ler_setores(self, arquivo_zip, pasta):
        with zipfile.ZipFile(arquivo_zip) as pacote:
            nome = next(n for n in pacote.namelist() if n.lower().endswith('.gpkg'))
            gpkg = pacote.extract(nome, pasta)
        conexao = sqlite3.connect(gpkg)
        colunas = ', '.join(f'"{c}"' for c in CAMPOS)
        features = []
        for blob, *valores in conexao.execute(f'select geom, {colunas} from Setores where geom is not null'):
            propriedades = dict(zip(CAMPOS.values(), valores))
            descricao = ' '.join((propriedades['descricao'] or '').split())
            if len(descricao) > TAMANHO_DESCRICAO:
                descricao = descricao[:TAMANHO_DESCRICAO].rsplit(' ', 1)[0] + '…'
            propriedades['descricao'] = descricao
            propriedades['local'] = ' '.join((propriedades['local'] or '').split())
            poligonos = [
                [[[round(x, 6), round(y, 6)] for x, y in anel] for anel in aneis]
                for aneis in enchente.geometria_gpkg(blob)
            ]
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'MultiPolygon', 'coordinates': poligonos},
                'properties': propriedades,
            })
        conexao.close()
        if not features:
            raise ErroDeColeta('o pacote do SGB não tem setores')
        return {'type': 'FeatureCollection', 'features': features}
