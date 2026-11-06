import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from dados import previsao, situacao
from dados.coletores import chuva, desmatamento, queimadas, saude, sismos
from dados.coletores.http import ColetaParcial
from dados.models import Coleta

FONTES = {
    'queimadas': queimadas.coletar,
    'desmatamento': desmatamento.coletar,
    'sismos': sismos.coletar,
    'chuva': chuva.coletar,
    'saude': saude.coletar,
    'previsao_chuva': previsao.coletar,  # só baixa quando sai rodada nova do Eta (a cada 12 h)
    # Por último: avalia as regionais do BC com os dados recém-coletados e atualiza os alertas.
    'situacao': situacao.coletar,
}
# Fontes que aceitam um período (opção --dias-<fonte>) e o padrão de cada uma.
DIAS_PADRAO = {'queimadas': 1, 'chuva': 3}


def executar(fonte, **parametros):
    """Roda um coletor e registra o resultado em Coleta. Nunca levanta exceção."""
    coleta = Coleta.objects.create(fonte=fonte, iniciada_em=timezone.now())
    try:
        coleta.registros = FONTES[fonte](**parametros)
        coleta.sucesso = True
    except ColetaParcial as erro:
        coleta.registros = erro.registros
        coleta.sucesso = True
        coleta.erro = str(erro)
    except Exception as erro:  # uma fonte fora do ar não derruba as outras
        coleta.erro = f'{erro.__class__.__name__}: {erro}'
    coleta.concluida_em = timezone.now()
    coleta.save()
    return coleta


class Command(BaseCommand):
    help = ('Coleta os dados abertos (INPE Queimadas, INPE DETER, sismos da USP, chuva do '
            'INPE/CPTEC MERGE, arboviroses do InfoDengue, chuva prevista pelo Eta), grava no banco e avalia a situação das regionais do BC.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--fonte', choices=sorted(FONTES), action='append',
            help='Coletar só esta fonte (pode repetir). Padrão: todas.',
        )
        parser.add_argument(
            '--loop', type=int, metavar='MINUTOS',
            help='Repetir a coleta a cada N minutos até ser interrompido (Ctrl+C).',
        )
        parser.add_argument(
            '--dias-queimadas', type=int, default=1, metavar='DIAS',
            help='Baixar os focos dos últimos N dias (1 a 60; o INPE guarda cerca de 30). '
                 'Com --loop, só na primeira rodada. Padrão: 1.',
        )
        parser.add_argument(
            '--dias-chuva', type=int, default=DIAS_PADRAO['chuva'], metavar='DIAS',
            help='Baixar a chuva dos últimos N dias (1 a 60) que ainda não estão no banco. '
                 'Com --loop, só na primeira rodada. Padrão: 3.',
        )

    def handle(self, *args, fonte=None, loop=None, **options):
        dias = {}
        for nome, padrao in DIAS_PADRAO.items():
            dias[nome] = options.get(f'dias_{nome}', padrao)
            if not 1 <= dias[nome] <= 60:
                raise CommandError(f'--dias-{nome} deve estar entre 1 e 60')
        fontes = fonte or list(FONTES)
        while True:
            for nome in fontes:
                self.stdout.write(f'Coletando {nome}...')
                parametros = {'dias': dias[nome]} if nome in dias else {}
                coleta = executar(nome, **parametros)
                duracao = (coleta.concluida_em - coleta.iniciada_em).total_seconds()
                if coleta.sucesso:
                    aviso = f' ({coleta.erro})' if coleta.erro else ''
                    self.stdout.write(self.style.SUCCESS(
                        f'  {coleta.registros} registros em {duracao:.0f}s{aviso}'
                    ))
                else:
                    self.stdout.write(self.style.ERROR(f'  falhou: {coleta.erro}'))
            if not loop:
                break
            dias = dict(DIAS_PADRAO)
            self.stdout.write(f'Próxima coleta em {loop} min.')
            time.sleep(loop * 60)
