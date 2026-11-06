from django.db import models


class Coleta(models.Model):
    """Uma execução de coleta de uma fonte, com o resultado."""

    fonte = models.CharField(max_length=40)
    iniciada_em = models.DateTimeField()
    concluida_em = models.DateTimeField(null=True, blank=True)
    sucesso = models.BooleanField(default=False)
    registros = models.PositiveIntegerField(default=0)
    erro = models.TextField(blank=True)

    class Meta:
        ordering = ['-iniciada_em']

    def __str__(self):
        status = 'ok' if self.sucesso else 'falhou'
        return f'{self.fonte} {self.iniciada_em:%d/%m %H:%M} ({status})'


class FocoQueimada(models.Model):
    """Foco de calor detectado por satélite (INPE Programa Queimadas)."""

    id_inpe = models.CharField(max_length=64, unique=True)
    data_hora = models.DateTimeField(db_index=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    satelite = models.CharField(max_length=20)
    municipio = models.CharField(max_length=120, blank=True)
    uf = models.CharField(max_length=2, db_index=True, blank=True)
    bioma = models.CharField(max_length=40, blank=True)
    frp = models.FloatField(null=True, blank=True, help_text='Potência radiativa do fogo (MW)')

    class Meta:
        ordering = ['-data_hora']
        verbose_name = 'foco de queimada'
        verbose_name_plural = 'focos de queimada'

    def __str__(self):
        return f'{self.municipio}/{self.uf} {self.data_hora:%d/%m %H:%M}'


class AlertaDesmatamento(models.Model):
    """Alerta de desmatamento ou degradação (INPE DETER)."""

    id_externo = models.CharField(max_length=80, unique=True)
    bioma = models.CharField(max_length=40)
    classe = models.CharField(max_length=60)
    data = models.DateField(db_index=True)
    area_km2 = models.FloatField()
    municipio = models.CharField(max_length=120, blank=True)
    uf = models.CharField(max_length=2, db_index=True, blank=True)
    satelite = models.CharField(max_length=40, blank=True)
    latitude = models.FloatField(help_text='Centro aproximado do polígono')
    longitude = models.FloatField(help_text='Centro aproximado do polígono')
    geometria = models.JSONField(help_text='Geometria GeoJSON (EPSG:4326)')

    class Meta:
        ordering = ['-data']
        verbose_name = 'alerta de desmatamento'
        verbose_name_plural = 'alertas de desmatamento'

    def __str__(self):
        return f'{self.classe} {self.municipio}/{self.uf} {self.data:%d/%m/%Y}'


class ChuvaDiaria(models.Model):
    """Chuva acumulada em 24 h numa grade regular sobre o Brasil (INPE/CPTEC MERGE).

    A grade vai do sul para o norte e do oeste para o leste; o centro da célula
    (i, j) fica em (lat_sul + i*passo, lon_oeste + j*passo). Os valores, em mm, ficam
    em `grade` como float32 comprimido com zlib (NaN onde não há dado): ~150 KB por dia.
    """

    data = models.DateField(unique=True, help_text='Dia em que terminam as 24 h (12h UTC, 9h de Brasília)')
    lat_sul = models.FloatField()
    lon_oeste = models.FloatField()
    passo = models.FloatField(help_text='Tamanho da célula em graus')
    linhas = models.PositiveIntegerField()
    colunas = models.PositiveIntegerField()
    grade = models.BinaryField()

    class Meta:
        ordering = ['-data']
        verbose_name = 'chuva diária'
        verbose_name_plural = 'chuvas diárias'

    def __str__(self):
        return f'Chuva {self.data:%d/%m/%Y}'

    @property
    def geometria(self):
        return (self.lat_sul, self.lon_oeste, self.passo, self.linhas, self.colunas)

    def valores(self):
        import zlib

        import numpy as np

        return np.frombuffer(zlib.decompress(self.grade), dtype='<f4').reshape(self.linhas, self.colunas)


class GradeHorariaEta(models.Model):
    """Um campo hora a hora de uma rodada do modelo Eta (INPE/CPTEC, 40 km).

    `grade` guarda `horas` campos seguidos (float32 comprimido com zlib, NaN sem dado): o
    campo k vale para a hora que termina em primeira_hora + k horas. Mesma orientação da
    ChuvaDiaria: linhas do sul para o norte, colunas do oeste para o leste.
    """

    rodada = models.DateTimeField(unique=True, help_text='Início da rodada (00 ou 12 UTC)')
    primeira_hora = models.DateTimeField(help_text='Fim da primeira hora prevista')
    horas = models.PositiveSmallIntegerField()
    lat_sul = models.FloatField()
    lon_oeste = models.FloatField()
    passo = models.FloatField(help_text='Tamanho da célula em graus')
    linhas = models.PositiveIntegerField()
    colunas = models.PositiveIntegerField()
    grade = models.BinaryField()

    class Meta:
        abstract = True
        ordering = ['-rodada']

    def valores(self):
        import zlib

        import numpy as np

        return np.frombuffer(zlib.decompress(self.grade), dtype='<f4').reshape(self.horas, self.linhas, self.colunas)


class PrevisaoChuva(GradeHorariaEta):
    """Chuva prevista na hora (mm)."""

    class Meta(GradeHorariaEta.Meta):
        verbose_name = 'previsão de chuva'
        verbose_name_plural = 'previsões de chuva'

    def __str__(self):
        return f'Previsão de chuva, rodada {self.rodada:%d/%m %Hh} UTC'


class PrevisaoTemperatura(GradeHorariaEta):
    """Temperatura máxima prevista na hora, a 2 m (°C)."""

    class Meta(GradeHorariaEta.Meta):
        verbose_name = 'previsão de temperatura'
        verbose_name_plural = 'previsões de temperatura'

    def __str__(self):
        return f'Previsão de temperatura, rodada {self.rodada:%d/%m %Hh} UTC'


class Sismo(models.Model):
    """Evento sísmico em território brasileiro (Centro de Sismologia da USP)."""

    id_evento = models.CharField(max_length=40, unique=True)
    data_hora = models.DateTimeField(db_index=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    profundidade_km = models.FloatField(null=True, blank=True)
    magnitude = models.FloatField(null=True, blank=True)
    tipo_magnitude = models.CharField(max_length=10, blank=True)
    local = models.CharField(max_length=200, blank=True)
    uf = models.CharField(max_length=2, db_index=True, blank=True)

    class Meta:
        ordering = ['-data_hora']

    def __str__(self):
        return f'M{self.magnitude} {self.local} {self.data_hora:%d/%m/%Y}'


class AvaliacaoSituacao(models.Model):
    """Uma avaliação da situação das regionais do BC: os indicadores de cada uma naquele momento."""

    momento = models.DateTimeField(db_index=True)
    regionais = models.JSONField(help_text='Lista de regionais com nível geral e indicadores')

    class Meta:
        ordering = ['-momento']
        verbose_name = 'avaliação da situação'
        verbose_name_plural = 'avaliações da situação'

    def __str__(self):
        return f'Situação {self.momento:%d/%m %H:%M}'


class AlertaRegional(models.Model):
    """Período em que um indicador de uma regional esteve em amarelo ou vermelho.

    Abre quando o indicador sai do verde e fecha (`fim`) quando volta a ele; guarda o pior
    nível e o texto do pico. Indicador indisponível não abre nem fecha alertas.
    """

    NIVEIS = [('amarelo', 'Amarelo'), ('vermelho', 'Vermelho')]

    cidade = models.CharField(max_length=30, db_index=True, help_text='Identificador em dados/cidades.py')
    indicador = models.CharField(max_length=30)
    nivel = models.CharField(max_length=10, choices=NIVEIS, help_text='Nível na última avaliação')
    nivel_maximo = models.CharField(max_length=10, choices=NIVEIS)
    inicio = models.DateTimeField(db_index=True)
    fim = models.DateTimeField(null=True, blank=True)
    atualizado_em = models.DateTimeField()
    texto = models.CharField(max_length=200, help_text='Situação na última avaliação')
    texto_pico = models.CharField(max_length=200, help_text='Situação no pior momento')

    class Meta:
        ordering = ['-inicio']
        verbose_name = 'alerta de regional'
        verbose_name_plural = 'alertas de regionais'

    def __str__(self):
        from django.utils.timezone import localtime

        return f'{self.cidade} · {self.indicador} ({self.nivel_maximo}) desde {localtime(self.inicio):%d/%m %H:%M}'


class BoletimSaude(models.Model):
    """Situação semanal de uma arbovirose numa das cidades em foco (InfoDengue, Fiocruz/FGV)."""

    NIVEIS = [(1, 'Verde'), (2, 'Amarelo'), (3, 'Laranja'), (4, 'Vermelho')]

    cidade = models.CharField(max_length=30, help_text='Identificador em dados/cidades.py')
    doenca = models.CharField(max_length=20)
    semana = models.PositiveIntegerField(help_text='Semana epidemiológica, AAAASS')
    inicio_semana = models.DateField()
    nivel = models.PositiveSmallIntegerField(choices=NIVEIS, help_text='1 condições desfavoráveis; 2 favoráveis à '
                                             'transmissão; 3 transmissão sustentada; 4 acima do limiar epidêmico')
    casos = models.PositiveIntegerField(null=True, blank=True, help_text='Casos notificados')
    casos_estimados = models.FloatField(null=True, blank=True, help_text='Casos estimados (corrige o atraso de notificação)')
    incidencia = models.FloatField(null=True, blank=True, help_text='Casos estimados por 100 mil habitantes')
    rt = models.FloatField(null=True, blank=True, help_text='Número de reprodução')

    class Meta:
        ordering = ['-semana', 'cidade', 'doenca']
        unique_together = [('cidade', 'doenca', 'semana')]
        verbose_name = 'boletim de saúde'
        verbose_name_plural = 'boletins de saúde'

    def __str__(self):
        return f'{self.cidade} {self.doenca} {self.semana} (nível {self.nivel})'
