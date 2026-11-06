from django.contrib import admin

from .models import (
    AlertaDesmatamento, AlertaRegional, AvaliacaoSituacao, BoletimSaude, ChuvaDiaria, Coleta, FocoQueimada, Sismo,
)


@admin.register(Coleta)
class ColetaAdmin(admin.ModelAdmin):
    list_display = ['fonte', 'iniciada_em', 'concluida_em', 'sucesso', 'registros']
    list_filter = ['fonte', 'sucesso']


@admin.register(FocoQueimada)
class FocoQueimadaAdmin(admin.ModelAdmin):
    list_display = ['data_hora', 'municipio', 'uf', 'bioma', 'satelite', 'frp']
    list_filter = ['uf', 'bioma', 'satelite']
    search_fields = ['municipio']
    date_hierarchy = 'data_hora'


@admin.register(AlertaDesmatamento)
class AlertaDesmatamentoAdmin(admin.ModelAdmin):
    list_display = ['data', 'classe', 'municipio', 'uf', 'bioma', 'area_km2']
    list_filter = ['bioma', 'uf', 'classe']
    search_fields = ['municipio']
    date_hierarchy = 'data'
    exclude = ['geometria']


@admin.register(Sismo)
class SismoAdmin(admin.ModelAdmin):
    list_display = ['data_hora', 'magnitude', 'local', 'uf', 'profundidade_km']
    list_filter = ['uf']
    date_hierarchy = 'data_hora'


@admin.register(ChuvaDiaria)
class ChuvaDiariaAdmin(admin.ModelAdmin):
    list_display = ['data', 'linhas', 'colunas', 'passo']
    date_hierarchy = 'data'
    exclude = ['grade']


@admin.register(AlertaRegional)
class AlertaRegionalAdmin(admin.ModelAdmin):
    list_display = ['cidade', 'indicador', 'nivel_maximo', 'inicio', 'fim', 'texto_pico']
    list_filter = ['cidade', 'indicador', 'nivel_maximo']
    date_hierarchy = 'inicio'


@admin.register(AvaliacaoSituacao)
class AvaliacaoSituacaoAdmin(admin.ModelAdmin):
    list_display = ['momento']
    date_hierarchy = 'momento'


@admin.register(BoletimSaude)
class BoletimSaudeAdmin(admin.ModelAdmin):
    list_display = ['semana', 'cidade', 'doenca', 'nivel', 'casos', 'incidencia']
    list_filter = ['cidade', 'doenca', 'nivel']
