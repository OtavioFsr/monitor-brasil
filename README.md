# Monitor Brasil

Mapa do Brasil com dados abertos de órgãos públicos brasileiros, em Python/Django.
Protótipo inspirado no [God's Eye View](https://github.com/bilawalsidhu/gods-eye-view),
mas em 2D e só com dados do Brasil.

## Camadas

| Camada | Fonte | Atualização | Observações |
|---|---|---|---|
| Focos de queimada | [INPE Programa Queimadas](https://terrabrasilis.dpi.inpe.br/queimadas/portal/), arquivos diários do Brasil | o INPE regrava o arquivo do dia ao longo do dia | Só satélites de órbita polar (VIIRS, MODIS, AVHRR); os geoestacionários (GOES, MSG) repetem o mesmo fogo a cada 10–15 min e inflariam as contagens. Exibidos como pontos ou como mapa de calor |
| Alertas de desmatamento | [INPE DETER](https://terrabrasilis.dpi.inpe.br/) (TerraBrasilis, WFS), Amazônia e Cerrado | publicações do DETER | Polígonos. 30 dias na primeira coleta; depois, 15 dias antes do último alerta gravado. O servidor oscila bastante |
| Sismos | [Centro de Sismologia da USP](https://www.sismo.iag.usp.br/) (FDSN) | contínua | Só eventos dentro de uma UF (teste de ponto-no-polígono na malha do IBGE) |
| Chuva acumulada | [INPE/CPTEC MERGE](https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/MERGE_READ-ME.pdf) (satélite GPM-IMERG combinado com pluviômetros), GRIB2 diário | um arquivo por dia, no fim da manhã | Grade de 0,1° (~11 km) recortada no Brasil (~100 KB por dia no banco). Cada dia são as 24 h até 9h de Brasília. A camada é uma imagem gerada no servidor, já na projeção do mapa e recortada pelo contorno do Brasil ou da UF; clicar no mapa mostra os mm do ponto |
| Raios | [GLM do GOES-19](https://ftp.cptec.inpe.br/goes/goes19/broadcast/glm/) (Geostationary Lightning Mapper), imagens JPG do INPE/CPTEC com `.jgw` | a cada 5 min, ~10 min de atraso; o INPE guarda ~8 dias | Cada imagem traz a densidade de "grupos" ópticos (cada descarga gera vários) por pixel de 0,02° (~2 km), em cores. Voltamos da cor ao número pelo brilho, comparando com a barra de cores do rodapé da própria imagem: o JPG guarda o brilho em resolução cheia e a cor pela metade, que num ponto pequeno se mistura com o preto. Acima de ~125 a barra vai do amarelo ao branco quase sem mudar de brilho, então os valores altos são aproximados. Juntamos os quadros dos últimos 15, 30 ou 60 min em áreas de 0,1° (~11 km) dentro do Brasil (ou da UF): cada área vira um círculo do tamanho dela, com a cor da maior densidade (fraca < 15, moderada < 60, forte < 150, muito forte), com um anel branco por dentro se teve raio nos últimos 15 min (sem anel, só antes: mostra por onde a tempestade passou). Cada quadro lido fica em cache por 3 h (só os novos são baixados, ~170 KB cada); nada vai para o banco. Camada desligada por padrão, atualiza a cada 2,5 min |
| Nuvens carregadas | [INPE/CPTEC, Divisão de Satélites](https://satelite.cptec.inpe.br/): GOES-19 canal 13 (infravermelho), tiles PNG | a cada 10 min; a imagem sai ~25–35 min depois do horário | O navegador busca os tiles direto do INPE e pinta só os topos de nuvem abaixo de −30 °C (quanto mais frio, mais alta e carregada; abaixo de −60 °C costuma ser tempestade). Nada é guardado no banco; o Django só consulta quais horários existem (cache de 3 min). A conversão tom de cinza → °C foi calibrada contra o NetCDF do mesmo horário (±3 °C). Enquanto a camada está ligada, um loop mostra as imagens reais de −30, −20 e −10 min, a mais recente ("agora") e três **projeções** (+10, +20, +30 min) que deslocam a imagem mais recente com o vento em 500 hPa do Eta (ver a linha do vento); como a imagem chega com ~30 min de atraso, o "+30 min" fica perto da hora real. A projeção só desloca: não forma, não cresce nem dissipa nuvens. Num teste em 31/10/2026 (casamento de blocos entre duas imagens com 30 min de diferença, 80 blocos sobre o Brasil), o vento em 500 hPa errou o movimento observado em 20 km/h na média, contra 27 km/h de supor a nuvem parada e 30 km/h do vento em 250 hPa. O slider escolhe um quadro e pausa |
| Chuva prevista (72 h) | [Modelo Eta 40 km, INPE/CPTEC](https://ftp.cptec.inpe.br/modelos/tempo/Eta/ams_40km/), GRIB1 por hora de previsão | rodadas às 00 e 12 UTC; o coletor grava a mais recente que já publicou 108 h | Camada do mapa com a soma das próximas 24, 48 ou 72 h, na mesma escala de cores da chuva acumulada (a grade de 0,4° é refinada para 0,1° por interpolação antes de pintar, e a soma anda com o relógio: a camada se refaz a cada 30 min); clicar no mapa mostra os mm previstos no ponto. Também aparece em cada card da situação das regionais (mm em 0–24, 24–48 e 48–72 h, coloridos pelas faixas diárias de chuva do INMET), no popup do prédio do BC e como indicador. O parâmetro 13 traz a chuva da hora anterior em metros (o `.ctl` diz "6h", mas os valores são horários: conferido em 31/10/2026). De cada hora baixamos só uma janela de 150 KB em volta da mensagem (~16 MB por rodada, em ~15 s, 4 downloads de cada vez); a grade horária da rodada fica no banco (`PrevisaoChuva`, ~250 KB, guardadas as 2 últimas). Valor no ponto do prédio por interpolação bilinear na grade de 40 km: é tendência, não precisão de bairro |
| Calor previsto (72 h) | Mesmos arquivos do Eta (parâmetro 6, temperatura máxima a 2 m na hora, em K) e as [normais climatológicas do INMET](https://portal.inmet.gov.br/normais) (média das máximas de cada mês, 1991-2020; no Rio, 1981-2010, porque a 1991-2020 não tem a estação do centro) | junto com a chuva prevista | Camada "Temperatura máxima" com a maior temperatura prevista nas próximas 24, 48 ou 72 h (de 28 a 44 °C; abaixo de 28 °C, transparente), refinada para 0,1° como a chuva prevista; clicar no mapa mostra a máxima do ponto. Cada card da situação das regionais mostra a máxima prevista de cada 24 h (cores: +3 °C acima da normal, +5 °C e 40 °C) e o indicador "Calor" compara com a normal do mês de cada dia. A janela baixada de cada hora passou a cobrir a temperatura e a chuva (~370 KB, ~40 MB por rodada); a temperatura fica em `PrevisaoTemperatura` (~1,5 MB por rodada). As normais estão copiadas em `dados/normais.py`. Atenção: é o modelo (40 km) contra a estação do INMET; em Belém o Eta tem ficado uns 4 °C acima da normal todo dia, o que parece viés do modelo |
| Vento (10 m e 500 hPa) | [Modelo Eta 40 km, INPE/CPTEC](https://ftp.cptec.inpe.br/modelos/tempo/Eta/ams_40km/), GRIB1 por hora de previsão | rodadas diárias; usamos a mais recente que tenha a hora atual | É **previsão** para a hora cheia atual, não medição. De cada arquivo (~12 MB) baixamos só o primeiro 1 MB (Range), onde estão u e v a 10 m (parâmetros 9 e 10 na numeração do CPTEC); para o vento em 500 hPa (~5,5 km, que move a projeção das nuvens), o trecho de 3,5 a 5,3 MB, onde ficam u e v nos níveis de pressão (parâmetros 56 e 57). Cada grade fica em cache por 30 min. O navegador desenha setas a cada 44 px da tela; clicar no mapa mostra velocidade e direção de onde vem o vento. As estações do INMET exigem token, e os modelos WRF/BAM e o Eta de 8 km deixaram de ser publicados no FTP do CPTEC em ago/2026 |
| Enchente do Guaíba (simulação) | Relevo: [ANADEM](https://metadados.snirh.gov.br/geonetwork/srv/api/records/a5b8f184-d4e3-45e6-a62b-e76d1f255f55) (ANA e IPH/UFRGS, 30 m). Porto Alegre e 2024: [banco das cheias da RH do Guaíba](https://zenodo.org/records/13227745) (IPH/UFRGS, CC-BY 4.0). Nível: telemetria da ANA, estação Cais Mauá C6 (87450004) | nível: de hora em hora (~30 min de atraso); relevo: estático | Slider de 0 a 7 m no Cais Mauá: alaga o terreno ligado ao Guaíba abaixo do nível ("de banheira", células de ~85 m). Em Porto Alegre valem as manchas simuladas pelo IPH, porque o ANADEM fica alto demais no centro (sobram prédios). Mostra a área alagada acima da faixa normal (1,30 m) por município, compara com a mancha observada por satélite em 06/05/2024 e desenha os diques. Não considera diques, casas de bombas nem a correnteza: a oeste (Eldorado do Sul, baixo Jacuí) a cheia real de 2024 foi maior que a simulada. A preparação (`preparar_enchente`) grava os arquivos em `dados/static/dados/enchente/` |
| Enchentes em Belém (maré e áreas de risco) | Maré: telemetria da ANA, estação Captação d'água da Cosanpa (31645000), a cada 15 min. Áreas de risco: [setorização de riscos geológicos de Belém](https://rigeo.sgb.gov.br/handle/doc/20761), SGB/CPRM, revisita de 2021 | maré: ~30 min de atraso; setores: estáticos | Em Belém a cheia vem da maré alta somada à chuva forte. Mostra a maré agora em relação à média de 30 dias (a régua não tem referência no nível do mar), se está enchendo ou vazando, a curva de 48 h e como a última maré alta se compara às dos últimos 30 dias; a chuva de 24 h no centro (MERGE); e os 125 setores de risco do SGB (inundação, alagamento, erosão) com pessoas e edificações. Sem simulação de nível: o ANADEM fica ~3 m acima do real nas baixadas (mediana de 4,8 m dentro dos setores de inundação). A preparação (`preparar_belem`) grava `dados/static/dados/enchente/belem_setores_risco.geojson` |
| Rios perto do BC (Recife, BH, Rio, Curitiba) | Telemetria da ANA: Rio Capibaribe em São Lourenço da Mata (39187800, SGB), Rio das Velhas em Raposos (41200430, SGB), Rio Tijuca na Capela Mayrink (59305071, INEA) e Rio Iguaçu na comporta do Canal Água Limpa (65006090, IAT-PR) | a cada 15 min; cache de 10 min (o "normal" do ano, de 24 h) | Na camada Enchentes, cada capital tem um painel com o nível agora, a variação em 6 h e o gráfico de 7 dias. Sem cotas oficiais de alerta acessíveis, o "normal" é o último ano da própria estação (~35 mil leituras): acima do percentil 95 é cheia (~18 dias por ano), acima do 99, cheia rara (< 4 dias). O nível de agora é a mediana das 3 últimas leituras, para um pico do sensor não virar alerta. Estações escolhidas numa varredura de out/2026 entre as telemétricas a até 40 km de cada prédio: muitas listadas como ativas não devolvem leituras; a do Arrudas (BH) está travada num valor só; em São Paulo, Brasília, Fortaleza e Salvador só sobram represas e usinas |
| Avisos do INMET | [API de avisos do INMET](https://avisos.inmet.gov.br/) (`apiprevmet3.inmet.gov.br/avisos/ativos`) | em vigor e próximos dias; cache de 10 min | Polígonos com a cor oficial (perigo potencial, perigo, grande perigo), vigência, riscos e instruções. Cada aviso traz os códigos IBGE dos municípios atingidos, o que permite cruzar exatamente com as regionais do BC. Camada ligada por padrão; os polígonos ficam por baixo das outras camadas e cobrem estados inteiros |
| Alertas do CEMADEN | [Painel de alertas do CEMADEN](https://painelalertas.cemaden.gov.br/) (`wsAlertas2`, JSON) | em vigor; cache de 10 min | Alertas de risco hidrológico (enchente, enxurrada) e de movimentos de massa (deslizamento) que o CEMADEN envia às Defesas Civis dos municípios, com nível (moderado, alto, muito alto) e o código IBGE do município. No mapa, só os 10 municípios das regionais do BC. Os alertas que a própria Defesa Civil manda à população (IDAP, por SMS e celular) são públicos por portaria, mas não achamos um endereço aberto de consulta |
| Dengue e chikungunya | [InfoDengue](https://info.dengue.mat.br/) (Fiocruz e FGV), API `alertcity` | semanal (semana epidemiológica); coletamos as últimas 10 semanas, que o InfoDengue revisa | Nas 10 cidades em foco: nível de alerta (1 a 4), casos notificados e estimados, incidência por 100 mil. Seção "Saúde nas regionais": a semana em datas, a situação de cada doença em palavras (baixo risco, clima favorável ao mosquito, transmissão sustentada, epidemia), casos por 100 mil e a tendência, um card por cidade (as de risco baixo nas duas doenças em verde, no fim). O InfoDengue aceita zika, mas hoje devolve lista vazia para ela |
| Banco Central | Endereços oficiais do BC ([Relatório da Administração 2015](https://www.bcb.gov.br/pre/surel/reladmbc/2015/msc/enderecos-bcb.pdf)); coordenadas do prédio no OpenStreetMap | estático | Sede em Brasília e as 9 representações regionais, em `dados/cidades.py`. Também alimentam o filtro "Cidade em foco", que aproxima o mapa no prédio do BC e filtra os dados pelo estado da cidade |
| Contorno das UFs | [IBGE, API de malhas](https://servicodados.ibge.gov.br/api/docs/malhas) | estático | `dados/static/dados/brasil_ufs.geojson` |

Cada coleta **acumula** no banco: o histórico cresce a cada execução, e o mapa
filtra por período e por estado.

## Situação das regionais e alertas

Para continuidade de negócios, o painel avalia em volta de cada prédio do Banco Central
(sede e 9 representações) os indicadores abaixo e dá a cada regional o pior nível entre eles.
Os limiares ficam em `dados/situacao.py` (`LIMIARES`).

| Indicador | Fonte | Amarelo | Vermelho |
|---|---|---|---|
| Aviso do INMET | aviso em vigor no município (códigos IBGE) | perigo potencial | perigo ou grande perigo |
| Alerta do CEMADEN | alerta em vigor no município (código IBGE) | moderado | alto ou muito alto |
| Tempestade | GOES-19 (INPE): topo de nuvem mais frio a até 20 km | ≤ −50 °C | ≤ −60 °C |
| Raios | GLM do GOES-19 (INPE), áreas de ~11 km com raio, distância até o centro da área | raio a até 20 km na última hora | raio a até 10 km nos últimos 15 min |
| Chuva 24 h | MERGE (INPE/CPTEC), no ponto do prédio | ≥ 30 mm | ≥ 50 mm |
| Chuva prevista | Eta (INPE/CPTEC), pior janela de 24 h nas próximas 72 h, no ponto do prédio | ≥ 50 mm | ≥ 100 mm |
| Calor | Eta (INPE/CPTEC), máxima de cada 24 h das próximas 72 h contra a normal do mês do INMET | 1 dia com máxima ≥ normal + 5 °C | 2 dias ou mais assim (onda de calor, pelo critério do INMET) ou máxima ≥ 40 °C |
| Vento | Eta (INPE/CPTEC), média a 10 m prevista para a hora | ≥ 30 km/h | ≥ 50 km/h |
| Queimadas | focos das últimas 24 h a até 15 km | 1 foco ou mais | foco a até 3 km ou 10 focos ou mais |
| Sismos | últimos 7 dias | M ≥ 2,5 a até 100 km | M ≥ 4,0 a até 200 km |
| Dengue e chikungunya | InfoDengue, semana mais recente | nível 3 (transmissão sustentada) | nível 4 (acima do limiar epidêmico) |
| Rio (Recife, BH, Rio, Curitiba) | ANA, estação perto do prédio, contra o último ano dela | acima do percentil 95 | acima do percentil 99 |
| Guaíba (Porto Alegre) | ANA, Cais Mauá | ≥ 2,55 m | ≥ 3,00 m |
| Maré e chuva (Belém) | ANA (maré) e MERGE (chuva) | maré alta entre as 10% maiores de 30 dias | isso e chuva ≥ 30 mm |

Fonte fora do ar ou dado velho deixa o indicador "sem dados" (cinza), sem mudar os alertas.
Cada avaliação é gravada (`AvaliacaoSituacao`, guardadas por 7 dias). Indicador fora do verde abre um
`AlertaRegional`, que guarda o pior nível e o texto do pico e é encerrado quando o indicador volta ao
verde. A avaliação roda como última fonte do `coletar` (a cada 30 min com `--loop 30`) e também quando
o painel é aberto e a última avaliação tem mais de 10 min. No mapa, cada prédio do BC ganha um anel
com a cor da regional; clicar no card (ou num alerta) aproxima a cidade.

## Como rodar

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (no Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python manage.py migrate
python manage.py coletar --dias-queimadas 30 --dias-chuva 30   # primeira coleta: alguns minutos
python manage.py preparar_enchente   # só se for refazer o relevo do simulador (já vem pronto; ~4 min, ~220 MB)
python manage.py preparar_belem      # idem para as áreas de risco de Belém (já vem pronto; ~116 MB)
python manage.py runserver
```

`--dias-queimadas 30` preenche o histórico de focos com os arquivos diários que o INPE
mantém (cerca de 30 dias); sem ele, a coleta traz só as últimas 24 h. `--dias-chuva 30`
baixa 30 dias de chuva do MERGE (o CPTEC guarda desde 1998, então dá para ir mais longe:
até 60 dias pela opção); sem ele, só os 3 últimos dias que ainda faltam no banco.

Abra http://localhost:8000.

Para manter os dados atualizados, deixe a coleta rodando em outro terminal:

```bash
python manage.py coletar --loop 30          # todas as fontes a cada 30 min
python manage.py coletar --fonte queimadas  # só uma fonte
```

Para ver e pesquisar os dados no painel administrativo do Django:

```bash
python manage.py createsuperuser
```

e abra http://localhost:8000/admin.

## API

Todas as rotas aceitam `?uf=PA` (sigla) e `?dias=N` (1 a 365).

| Rota | Padrão | Conteúdo |
|---|---|---|
| `/api/focos.geojson` | 24 h | Pontos, até 40 mil (os mais recentes) |
| `/api/focos-calor.json` | 24 h | Focos somados em células de 0,1° (~11 km): `[[lat, lon, focos], ...]`, sem limite; alimenta o mapa de calor |
| `/api/desmatamento.geojson` | 30 dias | Polígonos, até 10 mil (~1,5 KB cada) |
| `/api/sismos.geojson` | 90 dias | Pontos |
| `/api/chuva` | 1 dia | Período coberto (os últimos N dias gravados), média e máximo em mm, limites e endereço da imagem |
| `/api/chuva.png` | 1 dia | Imagem da chuva acumulada, em Web Mercator, transparente fora do Brasil (ou da UF) e abaixo de 1 mm |
| `/api/chuva/ponto` | 1 dia | `?lat=&lon=`: mm acumulados na célula do ponto e a UF |
| `/api/chuva-prevista` | 1 dia | Chuva prevista pelo Eta nos próximos `dias` (1 a 3): rodada, período, média e máximo em mm, limites e endereço da imagem |
| `/api/chuva-prevista.png` | 1 dia | Imagem da chuva prevista, como a da acumulada |
| `/api/chuva-prevista/ponto` | 1 dia | `?lat=&lon=`: mm previstos na célula do ponto |
| `/api/temperatura-prevista` | 1 dia | Maior temperatura prevista pelo Eta nos próximos `dias` (1 a 3): rodada, período, média e máxima em °C, limites e endereço da imagem |
| `/api/temperatura-prevista.png` | 1 dia | Imagem da temperatura máxima prevista |
| `/api/temperatura-prevista/ponto` | 1 dia | `?lat=&lon=`: máxima prevista na célula do ponto (°C, em `graus`) |
| `/api/rio` | — | `?cidade=` (recife, belo-horizonte, rio-de-janeiro, curitiba): estação, nível agora, variação em 6 h, percentis do último ano e série horária de 7 dias |
| `/api/raios` | — | Raios do GLM nos últimos `?minutos=` (15, 30 ou 60; padrão 30): horário da última imagem e áreas de 0,1° `[lat, lon, densidade, minutos antes da última imagem]`, das mais antigas para as mais novas; aceita `?uf=` |
| `/api/satelite` | — | Horário da imagem mais recente completa do GOES-19 no INPE, horários disponíveis (~3 h) e o modelo de endereço dos tiles (numeração TMS) |
| `/api/vento` | hora atual | Vento do Eta: u e v (m/s, linhas do sul para o norte), geometria da grade, hora válida e rodada; `?camada=10m` (padrão) ou `500hPa` |
| `/api/enchente` | — | Simulador do Guaíba: geometria da grade, endereços dos arquivos (relevo, alcance, municípios, mancha de 2024, diques), nível atual e das últimas 48 h no Cais Mauá, cotas de referência |
| `/api/enchente/belem` | — | Maré de Belém: nível agora em relação à média de 30 dias, tendência, curva de 48 h, estatísticas das marés altas; e o endereço das áreas de risco do SGB |
| `/api/situacao` | — | Situação de cada regional do BC: nível geral e indicadores (avaliação de até 10 min atrás) e os limiares |
| `/api/alertas` | — | Alertas abertos e histórico; `?cidade=` (ex.: `recife`) e `?limite=` (1 a 500, padrão 50) |
| `/api/avisos` | em vigor | Avisos do INMET como GeoJSON; `?quando=agora`, `proximos` ou `todos` |
| `/api/cemaden` | em vigor | Alertas de risco geo-hidrológico do CEMADEN como GeoJSON (um ponto por município) |
| `/api/saude` | — | Último boletim do InfoDengue (e a incidência da semana anterior) de cada cidade em foco |
| `/api/resumo` | — | Totais por UF (inclusive chuva média) e status da última coleta de cada fonte (aceita também `dias_desmatamento`, `dias_sismos` e `dias_chuva`) |

## Estrutura

```
dados/
  coletores/        um módulo por fonte: interpretar() (puro, testável) + coletar() (grava)
    queimadas.py    INPE Programa Queimadas
    desmatamento.py INPE DETER
    sismos.py       USP
    chuva.py        INPE/CPTEC MERGE (GRIB2 lido com ecCodes)
    saude.py        InfoDengue (dengue e chikungunya nas 10 cidades)
    geo.py          ponto-no-polígono, centro aproximado e máscara das UFs na grade de chuva
    ufs.py          tabela de UFs (código IBGE, sigla, nome)
    http.py         requisições com novas tentativas
  management/commands/coletar.py
  management/commands/preparar_enchente.py   relevo e manchas do simulador de enchente (rodar uma vez)
  management/commands/preparar_belem.py      áreas de risco do SGB em Belém (rodar uma vez)
  chuva.py          acumulado do período, média por UF e imagem PNG da camada
  satelite.py       horários das imagens do GOES-19 no INPE e a calibração das cores das nuvens
  raios.py          raios do GLM (GOES-19): leitura das imagens do INPE pela barra de cores e cache
  rios.py           nível dos rios perto do BC (ANA) comparado com o último ano de cada estação
  vento.py          vento a 10 m e em 500 hPa do modelo Eta (download parcial do GRIB1 e cache)
  previsao.py       chuva e temperatura máxima previstas pelo Eta nas próximas 72 h (coletor e série no ponto)
  normais.py        normais climatológicas do INMET (média das máximas mensais) das 10 capitais
  enchente.py       enchentes: simulador do Guaíba (ANADEM, IPH, projeções, alcance) e maré de Belém (ANA)
  situacao.py       situação das regionais do BC: indicadores, limiares e alertas com histórico
  avisos.py         avisos meteorológicos do INMET (cache de 10 min)
  cemaden.py        alertas de risco geo-hidrológico do CEMADEN (cache de 10 min)
  cidades.py        as 10 cidades em foco e o endereço/coordenadas do Banco Central em cada uma
  models.py         Coleta, FocoQueimada, AlertaDesmatamento, Sismo, ChuvaDiaria, AvaliacaoSituacao, AlertaRegional, BoletimSaude
  views.py          página do mapa e rotas GeoJSON
  templates/dados/mapa.html
  static/dados/     mapa.js (Leaflet), mapa.css, malha das UFs, ícone do prédio do BC (SVG)
```

## Testes

```bash
python manage.py test dados
```

Os testes não acessam a rede: as respostas das fontes são simuladas.

## Próximos passos possíveis

- **GeoDjango + PostGIS**: consultas espaciais no banco (focos por município, área desmatada por
  bioma, "o que há num raio de X km") em vez de filtros em Python. Requer PostgreSQL e GDAL.
- **Celery ou um agendador do sistema** no lugar de `coletar --loop`.
- **Mais fontes**: dados das estações do INMET (a API exige um token, pedido por e-mail ao
  INMET), ônibus do Rio (SMTR), PRODES
  (desmatamento anual).
- **Tiles vetoriais** (ou geometrias simplificadas por zoom) antes de publicar na internet: 30 dias de
  alertas do DETER somam ~12 MB de GeoJSON, rápido localmente, pesado numa conexão móvel.
