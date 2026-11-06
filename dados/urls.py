from django.urls import path

from . import views

app_name = 'dados'

urlpatterns = [
    path('', views.mapa, name='mapa'),
    path('api/focos.geojson', views.focos, name='focos'),
    path('api/focos-calor.json', views.focos_calor, name='focos_calor'),
    path('api/desmatamento.geojson', views.desmatamento, name='desmatamento'),
    path('api/sismos.geojson', views.sismos, name='sismos'),
    path('api/chuva', views.chuva, name='chuva'),
    path('api/chuva.png', views.chuva_png, name='chuva_png'),
    path('api/chuva/ponto', views.chuva_no_ponto, name='chuva_no_ponto'),
    path('api/chuva-prevista', views.chuva_prevista, name='chuva_prevista'),
    path('api/chuva-prevista.png', views.chuva_prevista_png, name='chuva_prevista_png'),
    path('api/chuva-prevista/ponto', views.chuva_prevista_no_ponto, name='chuva_prevista_no_ponto'),
    path('api/temperatura-prevista', views.temperatura_prevista, name='temperatura_prevista'),
    path('api/temperatura-prevista.png', views.temperatura_prevista_png, name='temperatura_prevista_png'),
    path('api/temperatura-prevista/ponto', views.temperatura_prevista_no_ponto, name='temperatura_prevista_no_ponto'),
    path('api/raios', views.raios_recentes, name='raios'),
    path('api/rio', views.rio_da_cidade, name='rio'),
    path('api/satelite', views.satelite_goes, name='satelite'),
    path('api/vento', views.vento_10m, name='vento'),
    path('api/enchente', views.enchente_guaiba, name='enchente'),
    path('api/enchente/belem', views.enchente_belem, name='enchente_belem'),
    path('api/situacao', views.situacao_regionais, name='situacao'),
    path('api/avisos', views.avisos_inmet, name='avisos'),
    path('api/cemaden', views.alertas_cemaden, name='cemaden'),
    path('api/saude', views.saude_regionais, name='saude'),
    path('api/alertas', views.alertas_regionais, name='alertas'),
    path('api/resumo', views.resumo, name='resumo'),
]
