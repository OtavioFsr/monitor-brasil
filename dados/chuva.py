"""Grades sobre o Brasil e a imagem do mapa: a chuva acumulada num período, das grades diárias
do MERGE. A chuva e a temperatura previstas (previsao.py) usam a mesma base."""

import io
import math
from datetime import timedelta

import numpy as np
from PIL import Image

from .coletores.geo import SIGLAS_DA_MASCARA, mascara_ufs
from .models import ChuvaDiaria

# Classes de chuva acumulada (mm, limite inferior) e suas cores. Abaixo de 1 mm, transparente.
CLASSES = [
    (1, '#cdeefc'), (5, '#97d3f4'), (10, '#5fb1e8'), (20, '#2f86d0'), (35, '#1f5fb0'),
    (50, '#3b3fa6'), (75, '#6a3d9a'), (100, '#94328f'), (150, '#c0237a'), (250, '#7a0b3f'),
]


class GradeNoMapa:
    """Valores numa grade regular (linhas do sul para o norte; centro da célula (i, j) em
    (lat_sul + i*passo, lon_oeste + j*passo)) com a máscara das UFs. As subclasses preenchem
    mm (o valor de cada célula, em mm ou °C), lat_sul, lon_oeste, passo, linhas, colunas e
    mascara; mm None é grade vazia. `classes` dá as cores da imagem: abaixo da primeira, transparente."""

    mm = None
    classes = CLASSES

    @property
    def vazio(self):
        return self.mm is None

    def _usar(self, mm, lat_sul, lon_oeste, passo):
        self.mm = mm
        self.lat_sul, self.lon_oeste, self.passo = lat_sul, lon_oeste, passo
        self.linhas, self.colunas = mm.shape
        self.mascara = mascara_ufs(lat_sul, lon_oeste, passo, self.linhas, self.colunas)

    def area(self, uf=''):
        """Células dentro do Brasil (ou da UF) com dado."""
        if uf:
            dentro = self.mascara == SIGLAS_DA_MASCARA.index(uf)
        else:
            dentro = self.mascara > 0
        return dentro & ~np.isnan(self.mm)

    def no_ponto(self, latitude, longitude):
        """Valor na célula do ponto; None fora do Brasil ou sem dado."""
        i = round((latitude - self.lat_sul) / self.passo)
        j = round((longitude - self.lon_oeste) / self.passo)
        if not (0 <= i < self.linhas and 0 <= j < self.colunas) or not self.mascara[i, j]:
            return None
        valor = self.mm[i, j]
        return None if np.isnan(valor) else round(float(valor), 1)

    def estatisticas(self, uf=''):
        """(média, máximo) dentro do Brasil ou da UF, ou (None, None)."""
        valores = self.mm[self.area(uf)]
        if not valores.size:
            return None, None
        return round(float(valores.mean()), 1), round(float(valores.max()), 1)

    def limites(self):
        """[[sul, oeste], [norte, leste]] das bordas da grade, para o L.imageOverlay."""
        meio = self.passo / 2
        return [
            [round(self.lat_sul - meio, 4), round(self.lon_oeste - meio, 4)],
            [round(self.lat_sul + (self.linhas - 1) * self.passo + meio, 4),
             round(self.lon_oeste + (self.colunas - 1) * self.passo + meio, 4)],
        ]

    def png(self, uf=''):
        """Imagem PNG da grade, já na projeção do Leaflet (Web Mercator).

        Cada linha da imagem pega a linha da grade na latitude correspondente; sem
        isso a grade sairia deslocada até ~100 km no sul do país.
        """
        classes = np.zeros(self.mm.shape, dtype=np.uint8)  # 0 = transparente
        mm = np.nan_to_num(self.mm, nan=-1)
        for indice, (limite, _) in enumerate(self.classes, start=1):
            classes[mm >= limite] = indice
        classes[~self.area(uf)] = 0

        (sul, _), (norte, _) = self.limites()
        y_sul, y_norte = _mercator(sul), _mercator(norte)
        altura = max(1, round(self.colunas * (y_norte - y_sul) / math.radians(self.colunas * self.passo)))
        y = y_norte - (np.arange(altura) + 0.5) * (y_norte - y_sul) / altura
        latitudes = np.degrees(2 * np.arctan(np.exp(y)) - math.pi / 2)
        linhas = np.clip(((latitudes - sul) / self.passo).astype(int), 0, self.linhas - 1)

        imagem = Image.fromarray(classes[linhas])  # 'L'; o putpalette abaixo o torna 'P'
        paleta = [0, 0, 0]
        for _, cor in self.classes:
            paleta += [int(cor[k:k + 2], 16) for k in (1, 3, 5)]
        imagem.putpalette(paleta)
        saida = io.BytesIO()
        imagem.save(saida, format='PNG', transparency=0, optimize=True)
        return saida.getvalue()


class Acumulado(GradeNoMapa):
    """Soma dos dias [inicio, fim] (os mais recentes gravados) e a máscara das UFs."""

    def __init__(self, dias):
        registros = list(ChuvaDiaria.objects.order_by('-data')[:1])
        self.dias_pedidos = dias
        self.inicio = self.fim = None
        self.dias = 0
        self.mm = None
        if not registros:
            return
        mais_recente = registros[0]
        self.fim = mais_recente.data
        geometria = mais_recente.geometria
        soma = None
        validos = None
        for dia in ChuvaDiaria.objects.filter(data__gt=self.fim - timedelta(days=dias)).order_by('data'):
            if dia.geometria != geometria:  # grade de outro formato: fica fora da soma
                continue
            valores = dia.valores()
            soma = np.nan_to_num(valores) if soma is None else soma + np.nan_to_num(valores)
            validos = ~np.isnan(valores) if validos is None else validos | ~np.isnan(valores)
            self.inicio = self.inicio or dia.data
            self.dias += 1
        self._usar(np.where(validos, soma, np.nan), *geometria[:3])

    def media_por_uf(self):
        """{sigla: mm médios} de cada UF com dado."""
        dentro = self.area()
        indices = self.mascara[dentro]
        somas = np.bincount(indices, weights=self.mm[dentro], minlength=len(SIGLAS_DA_MASCARA))
        celulas = np.bincount(indices, minlength=len(SIGLAS_DA_MASCARA))
        return {
            sigla: round(float(somas[i] / celulas[i]), 1)
            for i, sigla in enumerate(SIGLAS_DA_MASCARA) if sigla and celulas[i]
        }


def _mercator(latitude):
    return math.log(math.tan(math.pi / 4 + math.radians(latitude) / 2))
