// Mapa do Monitor Brasil: desenha as camadas servidas pelas rotas /api do Django.
(function () {
  'use strict';

  const { api, malhaUfs, iconeBancoCentral } = window.MONITOR;
  const BRASIL = L.latLngBounds([-34.2, -74.5], [5.6, -32.3]);

  const mapa = L.map('mapa', {
    preferCanvas: true, // milhares de pontos desenham bem melhor em canvas
    maxBounds: BRASIL.pad(0.4),
    minZoom: 4,
  }).fitBounds(BRASIL);

  const basemaps = {
    'Mapa (OpenStreetMap)': L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      // A política de uso dos tiles do OSM exige o Referer, e o Django manda
      // "Referrer-Policy: same-origin", que o omite em pedidos a outros sites.
      // Aqui vai só a origem (http://localhost:8000/), nunca o caminho.
      referrerPolicy: 'strict-origin-when-cross-origin',
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }),
    'Satélite (Esri)': L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 18, attribution: 'Imagens &copy; Esri, Maxar, Earthstar Geographics' },
    ),
  };
  basemaps['Mapa (OpenStreetMap)'].addTo(mapa);
  L.control.layers(basemaps, null, { position: 'topright' }).addTo(mapa);
  L.control.scale({ imperial: false }).addTo(mapa);
  // A chuva fica num painel próprio, entre o mapa de fundo (200) e as outras camadas (400).
  mapa.createPane('chuva').style.zIndex = 350;

  const formatoData = new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
  const formatoDia = new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeZone: 'UTC' });
  const numero = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 });

  const elUf = document.getElementById('uf');
  const camadas = {};
  let limitesUf = {};
  let contornos = null;

  // ---- Contorno das UFs (malha IBGE) -------------------------------------
  const CODIGO_PARA_SIGLA = {
    11: 'RO', 12: 'AC', 13: 'AM', 14: 'RR', 15: 'PA', 16: 'AP', 17: 'TO',
    21: 'MA', 22: 'PI', 23: 'CE', 24: 'RN', 25: 'PB', 26: 'PE', 27: 'AL', 28: 'SE', 29: 'BA',
    31: 'MG', 32: 'ES', 33: 'RJ', 35: 'SP', 41: 'PR', 42: 'SC', 43: 'RS',
    50: 'MS', 51: 'MT', 52: 'GO', 53: 'DF',
  };

  fetch(malhaUfs)
    .then((r) => r.json())
    .then((geojson) => {
      contornos = L.geoJSON(geojson, {
        interactive: false,
        style: { color: '#2b3a34', weight: 1, opacity: 0.6, fill: false },
      }).addTo(mapa);
      contornos.eachLayer((camada) => {
        const sigla = CODIGO_PARA_SIGLA[camada.feature.properties.codarea];
        if (sigla) limitesUf[sigla] = camada.getBounds();
      });
      contornos.bringToBack();
    });

  // ---- Estilos e popups ----------------------------------------------------
  function corDoFoco(frp) {
    if (frp >= 100) return '#7f0000';
    if (frp >= 30) return '#c92a2a';
    if (frp >= 10) return '#e8590c';
    return '#f59f00';
  }

  const escapar = (texto) =>
    String(texto).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

  // Popup em HTML: título e linhas "rótulo: valor" (valores vazios são omitidos; tudo escapado).
  function linhas(titulo, itens) {
    const corpo = itens
      .filter(([, valor]) => valor !== null && valor !== undefined && valor !== '')
      .map(([rotulo, valor]) => `${escapar(rotulo)}: ${escapar(valor)}`)
      .join('<br>');
    return `<strong>${escapar(titulo)}</strong>${corpo}`;
  }

  const DEFINICOES = {
    focos: {
      url: api.focos,
      desenhar: (geojson) =>
        L.geoJSON(geojson, {
          pointToLayer: (f, latlng) =>
            L.circleMarker(latlng, {
              bubblingMouseEvents: false, // o clique no foco não chega ao mapa (popup da chuva)
              radius: 3.5,
              stroke: false,
              fillColor: corDoFoco(f.properties.frp || 0),
              fillOpacity: 0.85,
            }),
          onEachFeature: (f, camada) => {
            const p = f.properties;
            camada.bindPopup(() =>
              linhas(`${p.municipio || 'Município não informado'}${p.uf ? ' / ' + p.uf : ''}`, [
                ['Detectado em', formatoData.format(new Date(p.data_hora))],
                ['Bioma', p.bioma],
                ['Satélite', p.satelite],
                ['Potência (FRP)', p.frp !== null ? `${numero.format(p.frp)} MW` : null],
              ]),
            );
          },
        }),
      contagem: (dados) =>
        `${numero.format(dados.features.length)} focos` +
        (dados.truncado ? ' (os mais recentes; o mapa de calor mostra o período todo)' : ''),
    },
    desmatamento: {
      url: api.desmatamento,
      desenhar: (geojson) =>
        L.geoJSON(geojson, {
          bubblingMouseEvents: false,
          style: { color: '#c92a2a', weight: 2, fillColor: '#ff6b6b', fillOpacity: 0.45 },
          onEachFeature: (f, camada) => {
            const p = f.properties;
            camada.bindPopup(() =>
              linhas(p.classe, [
                ['Local', `${p.municipio}${p.uf ? ' / ' + p.uf : ''}`],
                ['Bioma', p.bioma],
                ['Data da imagem', formatoDia.format(new Date(p.data))],
                ['Área', `${numero.format(p.area_km2)} km²`],
                ['Satélite', p.satelite],
              ]),
            );
          },
        }),
      contagem: (dados) =>
        `${numero.format(dados.features.length)} alertas` + (dados.truncado ? ' (mostrando os mais recentes)' : ''),
    },
    sismos: {
      url: api.sismos,
      desenhar: (geojson) =>
        L.geoJSON(geojson, {
          pointToLayer: (f, latlng) =>
            L.circleMarker(latlng, {
              bubblingMouseEvents: false,
              radius: 4 + Math.max(0, f.properties.magnitude || 0) * 2.5,
              color: '#4c2fb0',
              weight: 1.5,
              fillColor: '#6741d9',
              fillOpacity: 0.45,
            }),
          onEachFeature: (f, camada) => {
            const p = f.properties;
            camada.bindPopup(() =>
              linhas(`Magnitude ${p.magnitude ?? '?'} ${p.tipo_magnitude || ''}`, [
                ['Local', `${p.local}${p.uf ? ' (' + p.uf + ')' : ''}`],
                ['Data', formatoData.format(new Date(p.data_hora))],
                ['Profundidade', p.profundidade_km !== null ? `${numero.format(p.profundidade_km)} km` : null],
              ]),
            );
          },
        }),
      contagem: (dados) => `${numero.format(dados.features.length)} sismos`,
    },
    chuva: {
      url: api.chuva,
      // Imagem gerada no servidor (já recortada no Brasil ou na UF); sem dados, camada vazia.
      desenhar: (dados) =>
        dados.dias
          ? L.imageOverlay(dados.imagem, dados.limites, { pane: 'chuva', opacity: 0.75, interactive: false })
          : L.layerGroup(),
      contagem: (dados) => {
        if (!dados.dias) return 'sem dados: rode python manage.py coletar --fonte chuva';
        const ate = `até 9h de ${formatoDia.format(new Date(dados.fim))}`;
        const periodo = dados.dias === 1 ? `24 h ${ate}` : `${dados.dias} dias ${ate}`;
        if (dados.media_mm === null) return periodo;
        return `${periodo} · média ${numero.format(dados.media_mm)} mm · máx. ${numero.format(dados.maximo_mm)} mm`;
      },
    },
    previsao: {
      url: api.previsao,
      desenhar: (dados) =>
        dados.dias
          ? L.imageOverlay(dados.imagem, dados.limites, { pane: 'chuva', opacity: 0.75, interactive: false })
          : L.layerGroup(),
      contagem: (dados) => {
        if (!dados.dias) return 'sem previsão: rode python manage.py coletar --fonte previsao_chuva';
        const fim = new Date(dados.fim);
        const rodada = new Date(dados.rodada);
        const periodo = `até ${formatoDiaDaSemana.format(fim).replace('.', '')} ${formatoHoraCurta.format(fim)}h · rodada de ` +
          `${formatoDia.format(rodada)} ${String(rodada.getUTCHours()).padStart(2, '0')}h UTC`;
        if (dados.media_mm === null) return periodo;
        return `${periodo} · média ${numero.format(dados.media_mm)} mm · máx. ${numero.format(dados.maximo_mm)} mm`;
      },
    },
    temperatura: {
      url: api.temperatura,
      desenhar: (dados) =>
        dados.dias
          ? L.imageOverlay(dados.imagem, dados.limites, { pane: 'chuva', opacity: 0.7, interactive: false })
          : L.layerGroup(),
      contagem: (dados) => {
        if (!dados.dias) return 'sem previsão: rode python manage.py coletar --fonte previsao_chuva';
        const fim = new Date(dados.fim);
        const rodada = new Date(dados.rodada);
        const periodo = `até ${formatoDiaDaSemana.format(fim).replace('.', '')} ${formatoHoraCurta.format(fim)}h · rodada de ` +
          `${formatoDia.format(rodada)} ${String(rodada.getUTCHours()).padStart(2, '0')}h UTC`;
        if (dados.maximo_c === null) return periodo;
        return `${periodo} · até ${Math.round(dados.maximo_c)} °C`;
      },
    },
  };
  const formatoDiaDaSemana = new Intl.DateTimeFormat('pt-BR', { weekday: 'short', timeZone: 'America/Sao_Paulo' });
  const formatoHoraCurta = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', timeZone: 'America/Sao_Paulo' });

  // Focos como mapa de calor: o servidor soma os focos por célula da grade, então
  // não há limite de pontos. O Leaflet.heat soma de novo as células que caem no
  // mesmo pixel e corta num máximo fixo, o que satura tudo com 30 dias de dados
  // ou em zoom afastado. Aqui o máximo acompanha o que está na tela: é um
  // percentil alto das somas visíveis, recalculado a cada redesenho.
  const PERCENTIL_DO_MAXIMO = 0.98;
  const MapaDeCalor = L.HeatLayer.extend({
    _initCanvas() {
      L.HeatLayer.prototype._initCanvas.call(this);
      const heat = this._heat;
      const original = heat.data;
      heat.data = (pontos) => {
        const somas = pontos.map((p) => p[2]).sort((a, b) => a - b);
        heat.max(somas[Math.floor((somas.length - 1) * PERCENTIL_DO_MAXIMO)] || 1);
        return original.call(heat, pontos);
      };
    },
  });

  const elExibicaoFocos = document.getElementById('exibicao-focos');
  const elLegendaCalor = document.getElementById('legenda-calor');
  const elLegendaChuva = document.getElementById('legenda-chuva');
  const elLegendaPrevisao = document.getElementById('legenda-previsao');
  const elDicaPrevisao = document.getElementById('dica-previsao');
  const elLegendaTemperatura = document.getElementById('legenda-temperatura');
  const elDicaTemperatura = document.getElementById('dica-temperatura');
  const CALOR = {
    url: api.focosCalor,
    desenhar: (dados) =>
      new MapaDeCalor(dados.celulas, {
        radius: 12,
        blur: 14,
        max: Infinity, // o máximo de verdade vem do percentil acima
        maxZoom: 0, // sem atenuação por zoom: a escala já se ajusta
        minOpacity: 0.15,
        gradient: { 0.2: '#fed976', 0.4: '#fd8d3c', 0.6: '#f03b20', 0.8: '#bd0026', 1: '#67000d' },
      }),
    contagem: (dados) =>
      `${numero.format(dados.focos)} focos em ${numero.format(dados.celulas.length)} ` +
      `células de ~${Math.round(dados.tamanho_grau * 111)} km`,
  };

  function definicao(nome) {
    return nome === 'focos' && elExibicaoFocos.value === 'calor' ? CALOR : DEFINICOES[nome];
  }

  // ---- Carregamento --------------------------------------------------------
  const controles = {};
  for (const nome of Object.keys(DEFINICOES)) {
    controles[nome] = {
      ativo: document.querySelector(`[data-camada="${nome}"]`),
      periodo: document.querySelector(`[data-periodo="${nome}"]`),
      contagem: document.querySelector(`[data-contagem="${nome}"]`),
      pedido: null,
    };
  }

  async function carregar(nome) {
    const def = definicao(nome);
    const ctl = controles[nome];
    ctl.pedido?.abort();
    if (camadas[nome]) {
      mapa.removeLayer(camadas[nome]);
      delete camadas[nome];
    }
    if (nome === 'focos') elLegendaCalor.hidden = !(ctl.ativo.checked && def === CALOR);
    if (nome === 'chuva') elLegendaChuva.hidden = !ctl.ativo.checked;
    if (nome === 'previsao') elLegendaPrevisao.hidden = elDicaPrevisao.hidden = !ctl.ativo.checked;
    if (nome === 'temperatura') elLegendaTemperatura.hidden = elDicaTemperatura.hidden = !ctl.ativo.checked;
    if (!ctl.ativo.checked) {
      ctl.contagem.textContent = '';
      return;
    }
    const pedido = new AbortController();
    ctl.pedido = pedido;
    ctl.contagem.textContent = 'carregando…';
    const params = new URLSearchParams({ dias: ctl.periodo.value });
    if (elUf.value) params.set('uf', elUf.value);
    try {
      const resposta = await fetch(`${def.url}?${params}`, { signal: pedido.signal });
      if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
      const dados = await resposta.json();
      camadas[nome] = def.desenhar(dados).addTo(mapa);
      if (nome === 'sismos') camadas[nome].bringToFront();
      ctl.contagem.textContent = def.contagem(dados);
    } catch (erro) {
      if (erro.name !== 'AbortError') ctl.contagem.textContent = `erro ao carregar (${erro.message})`;
    }
  }

  // ---- Status das coletas ----------------------------------------------------
  const NOMES_FONTES = {
    queimadas: 'Queimadas (INPE)',
    desmatamento: 'Desmatamento (DETER)',
    sismos: 'Sismos (USP)',
    chuva: 'Chuva (INPE/CPTEC MERGE)',
    saude: 'Dengue e chikungunya (InfoDengue)',
    previsao_chuva: 'Previsão de chuva (Eta/CPTEC)',
    situacao: 'Situação das regionais',
  };

  async function carregarColetas() {
    const resposta = await fetch(api.resumo);
    if (!resposta.ok) return;
    const { coletas } = await resposta.json();

    const lista = document.getElementById('coletas');
    lista.replaceChildren(
      ...Object.entries(coletas).map(([fonte, c]) => {
        const li = document.createElement('li');
        const quando = c.ultimo_sucesso ? formatoData.format(new Date(c.ultimo_sucesso)) : 'nunca';
        li.textContent = `${NOMES_FONTES[fonte] || fonte}: ${quando}`;
        if (c.em_andamento) {
          li.textContent += ' — coletando agora…';
        } else if (c.sucesso === false) {
          li.className = 'falha';
          li.title = c.erro;
          li.textContent += ' — última tentativa falhou';
        } else if (c.sucesso) {
          li.className = 'ok';
          if (c.erro) li.title = c.erro;
        }
        return li;
      }),
    );
  }

  function atualizarTudo() {
    for (const nome of Object.keys(DEFINICOES)) carregar(nome);
    carregarColetas();
  }

  // ---- Interações ----------------------------------------------------------
  for (const [nome, ctl] of Object.entries(controles)) {
    ctl.ativo.addEventListener('change', () => carregar(nome));
    ctl.periodo.addEventListener('change', () => carregar(nome));
  }

  elExibicaoFocos.addEventListener('change', () => carregar('focos'));

  // Clique em área livre do mapa: a chuva (acumulada e/ou prevista) e o vento ali, conforme as camadas ligadas.
  async function consultarPonto(url, dias, lat, lng) {
    const params = new URLSearchParams({ lat: lat.toFixed(4), lon: lng.toFixed(4), dias });
    const resposta = await fetch(`${url}?${params}`);
    if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
    return resposta.json();
  }

  mapa.on('click', async (evento) => {
    const { chuva: ctlChuva, previsao: ctlPrevisao, temperatura: ctlTemperatura } = controles;
    const { lat, lng } = evento.latlng;
    const ventoAqui = elVento.checked && gradeVento ? ventoNoPonto(gradeVento, lat, lng) : null;
    const linhaVento = ventoAqui
      ? ['Vento', `${numero.format(Math.round(ventoAqui.kmh))} km/h, vindo de ${ventoAqui.cardeal} (${Math.round(ventoAqui.de)}°)`]
      : ['Vento', null];
    const coordenadas = ['Coordenadas', `${lat.toFixed(2)}, ${lng.toFixed(2)}`];
    if (!ctlChuva.ativo.checked && !ctlPrevisao.ativo.checked && !ctlTemperatura.ativo.checked) {
      if (ventoAqui) L.popup().setLatLng(evento.latlng).setContent(linhas('Vento a 10 m', [linhaVento, coordenadas])).openOn(mapa);
      return;
    }
    const popup = L.popup().setLatLng(evento.latlng).setContent('consultando…').openOn(mapa);
    try {
      const [observada, prevista, temperatura] = await Promise.all([
        ctlChuva.ativo.checked ? consultarPonto(api.chuvaNoPonto, ctlChuva.periodo.value, lat, lng) : null,
        ctlPrevisao.ativo.checked ? consultarPonto(api.previsaoNoPonto, ctlPrevisao.periodo.value, lat, lng) : null,
        ctlTemperatura.ativo.checked ? consultarPonto(api.temperaturaNoPonto, ctlTemperatura.periodo.value, lat, lng) : null,
      ]);
      const tem = (resposta, chave) => resposta?.[chave] !== null && resposta?.[chave] !== undefined;
      const temObservada = tem(observada, 'mm');
      const temPrevista = tem(prevista, 'mm');
      const temTemperatura = tem(temperatura, 'graus');
      if (!temObservada && !temPrevista && !temTemperatura) {
        const titulo = observada?.uf === '' ? 'Fora do território brasileiro' : 'Sem dado aqui';
        popup.setContent(linhas(titulo, [linhaVento, coordenadas]));
        return;
      }
      const dias = Number(ctlChuva.periodo.value);
      const proximas = (ctl) => `próximas ${Number(ctl.periodo.value) * 24} h`;
      const graus = temTemperatura ? `${Math.round(temperatura.graus)} °C` : null;
      let titulo;
      if (temObservada) titulo = `${numero.format(observada.mm)} mm de chuva`;
      else if (temPrevista) titulo = `${numero.format(prevista.mm)} mm previstos`;
      else titulo = `Máxima de ${graus} prevista`;
      popup.setContent(
        linhas(titulo, [
          ['Período', temObservada
            ? `${dias === 1 ? '24 h' : `${dias} dias`} até 9h de ${formatoDia.format(new Date(observada.fim))}`
            : `${proximas(temPrevista ? ctlPrevisao : ctlTemperatura)} (modelo Eta)`],
          ['Chuva prevista', temObservada && temPrevista ? `${numero.format(prevista.mm)} mm nas ${proximas(ctlPrevisao)}` : null],
          ['Máxima prevista', (temObservada || temPrevista) && temTemperatura ? `${graus} nas ${proximas(ctlTemperatura)}` : null],
          ['Estado', observada?.uf],
          linhaVento,
          coordenadas,
        ]),
      );
    } catch (erro) {
      popup.setContent(`Erro ao consultar (${erro.message})`);
    }
  });

  // As previsões andam com o relógio: refaz as camadas a cada 30 min.
  setInterval(() => {
    for (const nome of ['previsao', 'temperatura']) if (controles[nome].ativo.checked) carregar(nome);
  }, 30 * 60 * 1000);

  // ---- Banco Central e cidades em foco ------------------------------------
  const CIDADES = JSON.parse(document.getElementById('cidades').textContent);
  const ZOOM_CIDADE = 12;
  const elCidade = document.getElementById('cidade');
  const elBancoCentral = document.getElementById('banco-central');

  const icone = (largura) =>
    L.icon({
      iconUrl: iconeBancoCentral,
      iconSize: [largura, Math.round(largura * 1.125)],
      iconAnchor: [largura / 2, Math.round(largura * 1.125)], // a base do prédio fica no endereço
      popupAnchor: [0, -Math.round(largura * 1.05)],
      tooltipAnchor: [0, -Math.round(largura * 1.05)],
      className: 'marcador-bc',
    });
  const marcadoresBc = {};
  const camadaBc = L.layerGroup(
    CIDADES.map((cidade) => {
      const { bc } = cidade;
      const sede = bc.unidade === 'Sede';
      const marcador = L.marker([bc.lat, bc.lon], {
        icon: icone(sede ? 30 : 24),
        title: `Banco Central – ${cidade.nome}`,
        alt: `Banco Central – ${cidade.nome}`,
        zIndexOffset: sede ? 1000 : 500, // acima dos pontos e polígonos
      });
      marcador.bindTooltip(`Banco Central · ${cidade.nome}`, { direction: 'top' });
      marcador.bindPopup(() =>
        linhas(`Banco Central do Brasil · ${cidade.nome}`, [
          ['Unidade', bc.unidade],
          ['Endereço', bc.endereco],
          ['CEP', `${bc.cep} · ${cidade.nome}/${cidade.uf}`],
          ...linhasDaSituacao(cidade.id),
        ]),
      );
      marcadoresBc[cidade.id] = marcador;
      return marcador;
    }),
  ).addTo(mapa);

  elBancoCentral.addEventListener('change', () => {
    if (elBancoCentral.checked) camadaBc.addTo(mapa);
    else mapa.removeLayer(camadaBc);
  });

  elUf.addEventListener('change', () => {
    elCidade.value = ''; // escolher outro estado tira o foco da cidade
    mapa.fitBounds(limitesUf[elUf.value] || BRASIL);
    atualizarTudo();
  });

  // Cidade em foco: aproxima o mapa no prédio do BC e filtra os dados pelo estado da cidade.
  elCidade.addEventListener('change', () => {
    const cidade = CIDADES.find((c) => c.id === elCidade.value);
    if (!cidade) {
      mapa.fitBounds(limitesUf[elUf.value] || BRASIL);
      return;
    }
    const mudouUf = elUf.value !== cidade.uf;
    elUf.value = cidade.uf;
    mapa.setView([cidade.bc.lat, cidade.bc.lon], ZOOM_CIDADE);
    if (mudouUf) atualizarTudo();
    if (elBancoCentral.checked) marcadoresBc[cidade.id].openPopup();
  });

  // ---- Avisos do INMET -------------------------------------------------------
  // Polígonos com a cor oficial de cada severidade; os mais graves por cima. Ficam no
  // mesmo canvas das outras camadas, mas no fundo: um canvas num painel próprio ficaria
  // por baixo do canvas principal e não receberia os cliques.
  const elAvisos = document.getElementById('avisos');
  const elQuandoAvisos = document.getElementById('quando-avisos');
  const elContagemAvisos = document.getElementById('contagem-avisos');
  const elLegendaAvisos = document.getElementById('legenda-avisos');
  const ORDEM_SEVERIDADE = { 'Perigo Potencial': 1, Perigo: 2, 'Grande Perigo': 3 };
  const formatoAviso = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  let camadaAvisos = null;
  let pedidoAvisos = 0;

  async function carregarAvisos() {
    elQuandoAvisos.hidden = elLegendaAvisos.hidden = !elAvisos.checked;
    if (camadaAvisos) mapa.removeLayer(camadaAvisos);
    camadaAvisos = null;
    if (!elAvisos.checked) {
      elContagemAvisos.textContent = '';
      return;
    }
    const pedido = ++pedidoAvisos;
    elContagemAvisos.textContent = 'carregando…';
    try {
      const resposta = await fetch(`${api.avisos}?${new URLSearchParams({ quando: elQuandoAvisos.value })}`);
      const dados = await resposta.json();
      if (pedido !== pedidoAvisos) return;
      if (!resposta.ok) throw new Error(dados.erro || `HTTP ${resposta.status}`);
      dados.features.sort((a, b) => (ORDEM_SEVERIDADE[a.properties.severidade] || 0) - (ORDEM_SEVERIDADE[b.properties.severidade] || 0));
      camadaAvisos = L.geoJSON(dados, {
        bubblingMouseEvents: false,
        style: (f) => ({ color: f.properties.cor, weight: 1.5, fillColor: f.properties.cor, fillOpacity: 0.22 }),
        onEachFeature: (f, c) => {
          const a = f.properties;
          c.bindPopup(() => linhas(`${a.evento} · ${a.severidade}`, [
            ['Vigência', `${formatoAviso.format(new Date(a.inicio))} → ${formatoAviso.format(new Date(a.fim))}`],
            ['Estados', a.estados],
            ['Municípios', numero.format(a.municipios)],
            ['Riscos', a.riscos.join(' ')],
            ['Instruções', a.instrucoes.join(' ')],
          ]), { maxWidth: 340 });
        },
      }).addTo(mapa);
      camadaAvisos.bringToBack(); // focos, sismos e alertas continuam clicáveis por cima
      const n = dados.features.length;
      const quando = elQuandoAvisos.value === 'agora' ? 'em vigor agora' : 'nos próximos dias';
      elContagemAvisos.textContent = n ? `${n} ${n === 1 ? 'aviso' : 'avisos'} ${quando}` : `nenhum aviso ${quando}`;
    } catch (erro) {
      elContagemAvisos.textContent = `erro ao carregar (${erro.message})`;
    }
  }

  elAvisos.addEventListener('change', carregarAvisos);
  elQuandoAvisos.addEventListener('change', carregarAvisos);
  setInterval(() => elAvisos.checked && carregarAvisos(), 10 * 60 * 1000);
  carregarAvisos();

  // ---- Raios (sensor GLM do GOES-19, imagens de 5 min do INPE) -------------
  // Cada área de 0,1° (~11 km) com raio na janela vira um círculo do tamanho da área. A cor é
  // a intensidade (a maior densidade de grupos ópticos da janela); as áreas com raio nos
  // últimos 15 min ganham um anel branco por dentro, visível ao aproximar o mapa.
  const INTENSIDADES_RAIOS = [
    [150, '#862e9c', 'muito forte'], [60, '#e03131', 'forte'], [15, '#fd7e14', 'moderada'], [0, '#fab005', 'fraca'],
  ];
  const RECENTE_MIN = 10; // idade (até a última imagem) das 3 últimas imagens: ~15 min de raios
  const elRaios = document.getElementById('raios');
  const elJanelaRaios = document.getElementById('janela-raios');
  const elContagemRaios = document.getElementById('contagem-raios');
  const elLegendaRaios = document.getElementById('legenda-raios');
  const elDicaRaios = document.getElementById('dica-raios');
  let camadaRaios = null;
  let pedidoRaios = 0;

  // Raio do círculo: metade da área de 0,1° na tela (1 px no zoom do país, 18 px no 8), com um
  // mínimo para continuar visível de longe.
  function raioDoCirculo() {
    const celula = (0.1 * 256 * 2 ** mapa.getZoom()) / 360;
    return Math.min(14, Math.max(2, celula * 0.55));
  }
  // Anel: metade do círculo, com espessura proporcional; some quando o círculo tem menos de 3 px
  // (zoom do país), onde ele cobriria a cor da intensidade.
  const estiloDoAnel = (raio) => ({ radius: raio * 0.5, weight: Math.min(2, raio * 0.25), opacity: raio < 3 ? 0 : 0.95 });
  mapa.on('zoomend', () => {
    const raio = raioDoCirculo();
    camadaRaios?.eachLayer((c) => (c.options.anel ? c.setStyle(estiloDoAnel(raio)) : c.setRadius(raio)));
  });

  async function carregarRaios() {
    elJanelaRaios.hidden = elLegendaRaios.hidden = elDicaRaios.hidden = !elRaios.checked;
    if (!elRaios.checked) {
      if (camadaRaios) mapa.removeLayer(camadaRaios);
      camadaRaios = null;
      elContagemRaios.textContent = '';
      return;
    }
    const pedido = ++pedidoRaios;
    if (!camadaRaios) elContagemRaios.textContent = 'carregando…';
    const params = new URLSearchParams({ minutos: elJanelaRaios.value });
    if (elUf.value) params.set('uf', elUf.value);
    try {
      const resposta = await fetch(`${api.raios}?${params}`);
      const dados = await resposta.json();
      if (pedido !== pedidoRaios || !elRaios.checked) return;
      if (!resposta.ok) throw new Error(dados.erro || `HTTP ${resposta.status}`);
      const ultimo = new Date(dados.ultimo);
      const atraso = Math.max(0, Math.round((Date.now() - ultimo) / 60000));
      // Os mais intensos por cima.
      const ordenadas = [...dados.celulas].sort((a, b) => a[2] - b[2]);
      const raio = raioDoCirculo();
      const nova = L.layerGroup();
      for (const [lat, lon, densidade, idade] of ordenadas) {
        const [, cor, nome] = INTENSIDADES_RAIOS.find(([minimo]) => densidade >= minimo);
        const quando = new Date(ultimo.getTime() - idade * 60000);
        L.circleMarker([lat, lon], {
          radius: raio, stroke: false, fillColor: cor, fillOpacity: 0.85, bubblingMouseEvents: false,
        }).bindPopup(() => linhas(`Raios · intensidade ${nome}`, [
          ['Raio mais recente', `há ${idade + atraso} min (${formatoHora.format(quando)})`],
          ['Intensidade', `~${densidade} clarões (grupos ópticos) por pixel de ~2 km em 5 min`],
          ['Área', `~11 km em volta de ${lat.toFixed(2)}, ${lon.toFixed(2)}`],
        ])).addTo(nova);
        if (idade <= RECENTE_MIN) {
          L.circleMarker([lat, lon], { ...estiloDoAnel(raio), anel: true, color: '#fff', fill: false, interactive: false })
            .addTo(nova);
        }
      }
      nova.addTo(mapa);
      if (camadaRaios) mapa.removeLayer(camadaRaios); // troca só depois de desenhar a nova: sem piscar
      camadaRaios = nova;
      const n = dados.celulas.length;
      const recentes = dados.celulas.filter((c) => c[3] <= RECENTE_MIN).length;
      elContagemRaios.textContent = (n
        ? `raios em ${numero.format(n)} áreas de ~11 km, ${numero.format(recentes)} nos últimos 15 min`
        : 'nenhum raio no período') + ` · última imagem das ${formatoHora.format(ultimo)}, há ${atraso} min`;
    } catch (erro) {
      if (pedido === pedidoRaios) elContagemRaios.textContent = `erro ao carregar (${erro.message})`;
    }
  }

  elRaios.addEventListener('change', carregarRaios);
  elJanelaRaios.addEventListener('change', carregarRaios);
  elUf.addEventListener('change', carregarRaios);
  setInterval(() => elRaios.checked && carregarRaios(), 150 * 1000); // o INPE publica a cada 5 min
  carregarRaios();

  // ---- Alertas do CEMADEN ----------------------------------------------------
  const elCemaden = document.getElementById('cemaden');
  const elContagemCemaden = document.getElementById('contagem-cemaden');
  const elDicaCemaden = document.getElementById('dica-cemaden');
  let camadaCemaden = null;
  let pedidoCemaden = 0;

  async function carregarCemaden() {
    elDicaCemaden.hidden = !elCemaden.checked;
    if (camadaCemaden) mapa.removeLayer(camadaCemaden);
    camadaCemaden = null;
    if (!elCemaden.checked) {
      elContagemCemaden.textContent = '';
      return;
    }
    const pedido = ++pedidoCemaden;
    elContagemCemaden.textContent = 'carregando…';
    try {
      const resposta = await fetch(api.cemaden);
      const dados = await resposta.json();
      if (pedido !== pedidoCemaden) return;
      if (!resposta.ok) throw new Error(dados.erro || `HTTP ${resposta.status}`);
      camadaCemaden = L.geoJSON(dados, {
        pointToLayer: (f, latlng) => L.circleMarker(latlng, {
          bubblingMouseEvents: false, radius: 9, color: '#fff', weight: 2.5, fillColor: f.properties.cor, fillOpacity: 0.95,
        }),
        onEachFeature: (f, c) => {
          const a = f.properties;
          c.bindPopup(() => linhas(`${a.tipo} · ${a.nivel}`, [
            ['Município', `${a.municipio}/${a.uf}`],
            ['Emitido em', a.criado_em ? formatoAviso.format(new Date(a.criado_em)) : null],
            ['Atualizado em', a.atualizado_em && a.atualizado_em !== a.criado_em ? formatoAviso.format(new Date(a.atualizado_em)) : null],
            ['Fonte', 'CEMADEN, alerta enviado à Defesa Civil do município'],
          ]));
          c.bindTooltip(`${a.municipio}/${a.uf}: ${a.tipo.toLowerCase()} (${a.nivel.toLowerCase()})`, { direction: 'top' });
        },
      }).addTo(mapa);
      const n = dados.features.length;
      elContagemCemaden.textContent = n
        ? `${n} ${n === 1 ? 'regional com alerta' : 'regionais com alerta'} em vigor`
        : 'nenhuma regional com alerta em vigor';
    } catch (erro) {
      elContagemCemaden.textContent = `erro ao carregar (${erro.message})`;
    }
  }

  elCemaden.addEventListener('change', carregarCemaden);
  setInterval(() => elCemaden.checked && carregarCemaden(), 10 * 60 * 1000);
  carregarCemaden();

  // ---- Saúde nas regionais (InfoDengue) -------------------------------------
  // Um card por cidade, das mais graves para as menos (as de risco baixo, em verde, no fim).
  const SITUACAO_SAUDE = { 1: 'Baixo risco', 2: 'Clima favorável', 3: 'Transmissão sustentada', 4: 'Epidemia' };
  const DESCRICAO_SAUDE = {
    1: 'condições desfavoráveis à transmissão',
    2: 'clima favorável ao mosquito, ainda sem transmissão sustentada',
    3: 'transmissão sustentada',
    4: 'incidência acima do limiar epidêmico',
  };
  const formatoDiaMes = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', timeZone: 'UTC' });
  const elSaudeCidades = document.getElementById('saude-cidades');

  function tendencia(d) {
    if (d.incidencia === null || d.incidencia_anterior === null) return null;
    if (d.incidencia_anterior === 0) return d.incidencia >= 0.5 ? 'subindo' : null;
    const variacao = (d.incidencia - d.incidencia_anterior) / d.incidencia_anterior;
    if (variacao >= 0.1) return 'subindo';
    if (variacao <= -0.1) return 'caindo';
    return null;
  }

  function linhaDaDoenca(d) {
    const linha = document.createElement('div');
    linha.className = 'doenca';
    const nome = document.createElement('span');
    nome.className = 'nome';
    nome.textContent = d.nome;
    const selo = document.createElement('span');
    selo.className = `selo n${d.nivel}`;
    selo.textContent = SITUACAO_SAUDE[d.nivel];
    const numero_ = document.createElement('span');
    numero_.className = 'numero';
    numero_.textContent = d.incidencia === null ? '' : `${numero.format(Math.round(d.incidencia * 10) / 10)} por 100 mil`;
    const rumo = tendencia(d);
    if (rumo) {
      const seta = document.createElement('span');
      seta.className = `tendencia-${rumo}`;
      seta.textContent = rumo === 'subindo' ? ' ↑ subindo' : ' ↓ caindo';
      numero_.append(seta);
    }
    linha.title = `${d.nome}: ${DESCRICAO_SAUDE[d.nivel]}. ${d.casos ?? '?'} casos notificados na semana, ` +
      `${numero.format(Math.round(d.casos_estimados ?? 0))} estimados.`;
    linha.append(nome, selo, numero_);
    return linha;
  }

  function juntarNomes(nomes) {
    return nomes.length > 1 ? `${nomes.slice(0, -1).join(', ')} e ${nomes.at(-1)}` : nomes[0];
  }

  async function carregarSaude() {
    try {
      const resposta = await fetch(api.saude);
      if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
      const { cidades } = await resposta.json();
      const comDados = cidades.filter((c) => Object.keys(c.doencas).length);
      const doencas = (c) => Object.values(c.doencas);
      const pior = (c) => Math.max(...doencas(c).map((d) => d.nivel));
      const maiorIncidencia = (c) => Math.max(...doencas(c).map((d) => d.incidencia ?? 0));

      const semana = comDados.flatMap(doencas).sort((a, b) => b.semana - a.semana)[0];
      if (semana) {
        const inicio = new Date(semana.inicio_semana);
        const fim = new Date(inicio.getTime() + 6 * 86400000);
        document.getElementById('saude-intro').textContent =
          `Semana de ${formatoDiaMes.format(inicio)} a ${formatoDiaMes.format(fim)} (semana epidemiológica ` +
          `${semana.semana % 100}), segundo o InfoDengue (Fiocruz/FGV).`;
      }

      const ordenadas = [...comDados]
        .sort((a, b) => pior(b) - pior(a) || maiorIncidencia(b) - maiorIncidencia(a));
      elSaudeCidades.replaceChildren(
        ...ordenadas.map((c) => {
          const li = document.createElement('li');
          li.className = `saude-cidade pior-${pior(c)}`;
          const botao = document.createElement('button');
          botao.type = 'button';
          botao.dataset.cidade = c.id;
          const nome = document.createElement('strong');
          nome.textContent = c.nome;
          const uf = document.createElement('span');
          uf.className = 'uf';
          uf.textContent = c.uf;
          botao.append(nome, uf, ...doencas(c).map(linhaDaDoenca));
          li.append(botao);
          return li;
        }),
      );
      const semDados = cidades.filter((c) => !Object.keys(c.doencas).length).map((c) => c.nome);
      document.getElementById('saude-baixo').textContent = semDados.length ? `Sem dados: ${juntarNomes(semDados)}.` : '';
    } catch (erro) {
      document.getElementById('saude-intro').textContent = `Erro ao carregar os dados de saúde (${erro.message}).`;
    }
  }

  elSaudeCidades.addEventListener('click', (evento) => {
    const alvo = evento.target.closest('[data-cidade]');
    if (!alvo) return;
    elCidade.value = alvo.dataset.cidade;
    elCidade.dispatchEvent(new Event('change'));
  });
  carregarSaude();

  // ---- Situação das regionais e alertas -----------------------------------
  // O servidor avalia cada regional (tempestade, chuva, vento, queimadas, sismos e, em
  // Porto Alegre e Belém, rio e maré) e mantém os alertas; aqui só mostramos.
  const NIVEIS = {
    verde: { nome: 'normal', cor: '#2f9e44' },
    amarelo: { nome: 'atenção', cor: '#f59f00' },
    vermelho: { nome: 'alerta', cor: '#e03131' },
    indisponivel: { nome: 'sem dados', cor: '#adb5bd' },
  };
  const elRegionais = document.getElementById('regionais');
  const elSituacaoResumo = document.getElementById('situacao-resumo');
  const elSituacaoRodape = document.getElementById('situacao-rodape');
  const elAlertasAbertos = document.getElementById('alertas-abertos');
  const elAlertasHistorico = document.getElementById('alertas-historico');
  const elContadorAlertas = document.getElementById('contador-alertas');
  const elTituloAlertasAbertos = document.getElementById('titulo-alertas-abertos');
  const formatoCurto = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  const formatoHorario = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit' });
  const aneisBc = L.layerGroup();
  if (elBancoCentral.checked) aneisBc.addTo(mapa);
  let situacao = null;

  function linhasDaSituacao(id) {
    const regional = situacao?.regionais.find((r) => r.id === id);
    if (!regional) return [];
    return [
      ['Situação', NIVEIS[regional.nivel].nome],
      ...regional.indicadores
        .filter((i) => i.nivel !== 'verde' || i.id === 'chuva_prevista' || i.id === 'calor') // as previsões aparecem sempre
        .map((i) => [i.nome, i.texto]),
    ];
  }

  function duracao(inicio, fim) {
    const minutos = Math.max(0, Math.round((fim - inicio) / 60000));
    if (minutos < 60) return `${minutos} min`;
    const horas = Math.floor(minutos / 60);
    if (horas < 48) return `${horas} h${minutos % 60 ? ` ${minutos % 60} min` : ''}`;
    return `${Math.round(horas / 24)} dias`;
  }

  // Previsões do Eta nos cards: uma caixinha para cada 24 h das próximas 72 h.
  function linhaDePrevisao(titulo, indicador, selos) {
    const linha = document.createElement('div');
    linha.className = 'previsao-dias';
    linha.title = `${indicador.texto} (modelo Eta/CPTEC)`;
    const rotulo = document.createElement('span');
    rotulo.textContent = titulo;
    linha.append(rotulo);
    selos.forEach(([texto, classe], k) => {
      const selo = document.createElement('span');
      selo.className = `dia ${classe}`;
      const janela = document.createElement('small');
      janela.textContent = `${k * 24}–${(k + 1) * 24} h`;
      selo.append(janela, texto);
      linha.append(selo);
    });
    return linha;
  }

  // Chuva: faixas diárias dos avisos do INMET (30, 50 e 100 mm).
  const FAIXAS_CHUVA_PREVISTA = [[100, 'p5'], [50, 'p4'], [30, 'p3'], [10, 'p2'], [1, 'p1'], [0, 'p0']];
  const linhaChuvaPrevista = (indicador) => linhaDePrevisao('Chuva prevista (Eta)', indicador, indicador.dias_mm.map((mm) => [
    `${numero.format(mm < 10 ? Math.round(mm * 10) / 10 : Math.round(mm))} mm`,
    FAIXAS_CHUVA_PREVISTA.find(([minimo]) => mm >= minimo)[1],
  ]));

  // Calor: a máxima de cada 24 h contra a normal do mês do INMET (+3 °C quente, +5 °C muito
  // acima, que repetido em 2 dias é onda de calor) e 40 °C, extremo em qualquer capital.
  function classeDoCalor(graus, normal) {
    if (graus >= 40) return 'p5';
    if (graus >= normal + 5) return 'p4';
    if (graus >= normal + 3) return 'p3';
    return 'p0';
  }
  const linhaCalorPrevisto = (indicador) => linhaDePrevisao(
    `Máxima prevista (normal: ${numero.format(indicador.normais_c[0])} °C)`, indicador,
    indicador.dias_c.map((graus, k) => [`${Math.round(graus)} °C`, classeDoCalor(graus, indicador.normais_c[k])]),
  );

  function desenharSituacao() {
    const { regionais } = situacao;
    const contagem = (nivel) => regionais.filter((r) => r.nivel === nivel).length;
    const partes = [
      [contagem('vermelho'), 'em alerta'], [contagem('amarelo'), 'em atenção'],
      [contagem('verde'), contagem('verde') === 1 ? 'normal' : 'normais'], [contagem('indisponivel'), 'sem dados'],
    ].filter(([n]) => n);
    elSituacaoResumo.textContent = partes.map(([n, rotulo]) => `${n} ${rotulo}`).join(' · ');

    // As mais graves primeiro; dentro de cada nível, de norte a sul (ordem de CIDADES).
    const gravidade = { vermelho: 0, amarelo: 1, indisponivel: 2, verde: 3 };
    const ordenadas = [...regionais].sort((x, y) => gravidade[x.nivel] - gravidade[y.nivel]);
    elRegionais.replaceChildren(
      ...ordenadas.map((r) => {
        const li = document.createElement('li');
        li.className = `regional nivel-${r.nivel}${r.id === elCidade.value ? ' ativa' : ''}`;
        const botao = document.createElement('button');
        botao.type = 'button';
        botao.dataset.cidade = r.id;
        botao.title = `${r.nome}: ${NIVEIS[r.nivel].nome}`;
        const semaforo = document.createElement('span');
        semaforo.className = 'semaforo';
        const nome = document.createElement('strong');
        nome.textContent = r.nome;
        const uf = document.createElement('span');
        uf.className = 'uf';
        uf.textContent = r.uf;
        const motivos = document.createElement('ul');
        motivos.className = 'motivos';
        const foraDoNormal = r.indicadores.filter((i) => i.nivel === 'amarelo' || i.nivel === 'vermelho');
        for (const i of foraDoNormal) {
          const item = document.createElement('li');
          item.textContent = `${i.nome}: ${i.texto}`;
          motivos.append(item);
        }
        const semDados = r.indicadores.filter((i) => i.nivel === 'indisponivel').map((i) => i.nome.toLowerCase());
        if (semDados.length) {
          const item = document.createElement('li');
          item.className = 'indisponivel';
          item.textContent = `sem dados: ${semDados.join(', ')}`;
          motivos.append(item);
        }
        botao.append(semaforo, nome, uf);
        if (motivos.children.length) botao.append(motivos);
        const prevista = r.indicadores.find((i) => i.id === 'chuva_prevista' && i.dias_mm);
        if (prevista) botao.append(linhaChuvaPrevista(prevista));
        const calor = r.indicadores.find((i) => i.id === 'calor' && i.dias_c);
        if (calor) botao.append(linhaCalorPrevisto(calor));
        li.append(botao);
        return li;
      }),
    );

    aneisBc.clearLayers();
    for (const r of regionais) {
      const { bc } = CIDADES.find((c) => c.id === r.id);
      const cor = NIVEIS[r.nivel].cor;
      L.circleMarker([bc.lat, bc.lon], {
        radius: 19, color: cor, weight: 3, fillColor: cor, fillOpacity: r.nivel === 'verde' ? 0.08 : 0.22, interactive: false,
      }).addTo(aneisBc);
    }
    elSituacaoRodape.textContent =
      `Avaliado às ${formatoHorario.format(new Date(situacao.avaliado_em))}; atualiza a cada 5 min. ` +
      'Raios em volta do prédio do BC: tempestade e raios 20 km, queimadas 15 km, sismos 100–200 km.';
  }

  function itemDeAlerta(a, aberto) {
    const li = document.createElement('li');
    li.className = `alerta nivel-${aberto ? a.nivel : a.nivel_maximo}`;
    li.dataset.cidade = a.cidade;
    const semaforo = document.createElement('span');
    semaforo.className = 'semaforo';
    const texto = document.createElement('div');
    const titulo = document.createElement('strong');
    const cidade = CIDADES.find((c) => c.id === a.cidade);
    titulo.textContent = `${cidade ? cidade.nome : a.cidade} · ${a.nome_indicador}`;
    texto.append(titulo, ` — ${aberto ? a.texto : a.texto_pico}`);
    const detalhe = document.createElement('small');
    const inicio = new Date(a.inicio);
    detalhe.textContent = aberto
      ? `desde ${formatoCurto.format(inicio)} (há ${duracao(inicio, Date.now())})` +
        (a.nivel_maximo !== a.nivel ? ` · chegou a ${NIVEIS[a.nivel_maximo].nome}` : '')
      : `${formatoCurto.format(inicio)} → ${formatoCurto.format(new Date(a.fim))} (${duracao(inicio, new Date(a.fim))}) · ` +
        `pior: ${NIVEIS[a.nivel_maximo].nome}`;
    li.append(semaforo, texto, detalhe);
    return li;
  }

  function listaOuVazio(elemento, itens, vazio) {
    if (itens.length) {
      elemento.replaceChildren(...itens);
      return;
    }
    const li = document.createElement('li');
    li.className = 'vazio';
    li.textContent = vazio;
    elemento.replaceChildren(li);
  }

  async function carregarAlertas() {
    const params = new URLSearchParams({ limite: 30 });
    if (elCidade.value) params.set('cidade', elCidade.value);
    try {
      const resposta = await fetch(`${api.alertas}?${params}`);
      if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
      const { abertos, historico } = await resposta.json();
      const cidade = CIDADES.find((c) => c.id === elCidade.value);
      elTituloAlertasAbertos.textContent = cidade ? `Abertos em ${cidade.nome}` : 'Abertos';
      elContadorAlertas.textContent = abertos.length ? abertos.length : '';
      listaOuVazio(elAlertasAbertos, abertos.map((a) => itemDeAlerta(a, true)), 'Nenhum alerta aberto.');
      listaOuVazio(elAlertasHistorico, historico.map((a) => itemDeAlerta(a, false)), 'Nenhum alerta encerrado ainda.');
    } catch (erro) {
      listaOuVazio(elAlertasAbertos, [], `Erro ao carregar os alertas (${erro.message}).`);
    }
  }

  async function carregarSituacao() {
    try {
      const resposta = await fetch(api.situacao);
      if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
      situacao = await resposta.json();
      desenharSituacao();
    } catch (erro) {
      elSituacaoResumo.textContent = `Erro ao avaliar as regionais (${erro.message}).`;
    }
    carregarAlertas();
  }

  const focarCidade = (evento) => {
    const alvo = evento.target.closest('[data-cidade]');
    if (!alvo) return;
    elCidade.value = alvo.dataset.cidade;
    elCidade.dispatchEvent(new Event('change'));
  };
  elRegionais.addEventListener('click', focarCidade);
  elAlertasAbertos.addEventListener('click', focarCidade);
  elAlertasHistorico.addEventListener('click', focarCidade);
  elCidade.addEventListener('change', () => {
    if (situacao) desenharSituacao();
    carregarAlertas();
  });
  elBancoCentral.addEventListener('change', () => {
    if (elBancoCentral.checked) aneisBc.addTo(mapa);
    else mapa.removeLayer(aneisBc);
  });
  setInterval(carregarSituacao, 5 * 60 * 1000);
  carregarSituacao();

  // Seções minimizáveis: cada navegador lembra se ficaram abertas ou fechadas
  // (v2: os padrões mudaram com as caixas de alertas e saúde, então o que foi salvo antes é ignorado).
  for (const secao of document.querySelectorAll('details.secao')) {
    const chave = `monitor:v2:${secao.id}`;
    try {
      const salvo = localStorage.getItem(chave);
      if (salvo !== null) secao.open = salvo === 'aberta';
    } catch { /* armazenamento indisponível: fica o padrão da página */ }
    secao.addEventListener('toggle', () => {
      try {
        localStorage.setItem(chave, secao.open ? 'aberta' : 'fechada');
      } catch { /* idem */ }
    });
  }

  // ---- Nuvens carregadas (GOES-19 canal 13, tiles do INPE) ----------------
  // Os tiles vêm em tons de cinza (mais branco = topo mais frio). Cada tile é
  // desenhado num canvas e recolorido: só os topos abaixo de -30 °C aparecem,
  // com a cor da classe de temperatura (calibração em dados/satelite.py).
  mapa.createPane('nuvens').style.zIndex = 360; // acima da chuva, abaixo das outras camadas
  const CLASSES_NUVENS = JSON.parse(document.getElementById('classes-nuvens').textContent);
  const COR_POR_CINZA = new Uint8ClampedArray(256 * 4);
  for (let cinza = 0; cinza < 256; cinza++) {
    let classe = null;
    for (const c of CLASSES_NUVENS) if (cinza >= c.cinza) classe = c; // a mais fria que o tom alcança
    if (!classe) continue;
    COR_POR_CINZA.set(
      [parseInt(classe.cor.slice(1, 3), 16), parseInt(classe.cor.slice(3, 5), 16),
        parseInt(classe.cor.slice(5, 7), 16), classe.opacidade],
      cinza * 4,
    );
  }

  // Animação: os quadros reais de até 30 min antes da imagem mais recente e três
  // projeções (+10, +20, +30 min), feitas deslocando a imagem mais recente com o
  // vento em 500 hPa do Eta (~5,5 km de altura), que é o que costuma conduzir as
  // nuvens de tempestade. A projeção só desloca: não cria nem dissipa nuvens.
  const TILE = 256;
  const PASSO_QUADROS_MIN = 10;
  const QUADROS_ANTES = 3;
  const QUADROS_DEPOIS = 3;
  const MALHA_VENTO = 16; // px entre os pontos onde o vento é calculado em cada tile
  const MAXIMO_TILES_GUARDADOS = 200;
  const tilesCinza = new Map(); // url → Promise dos pixels RGBA do tile em cinza (null se ausente)

  function pixelsDoTile(url) {
    if (!tilesCinza.has(url)) {
      if (tilesCinza.size >= MAXIMO_TILES_GUARDADOS) tilesCinza.delete(tilesCinza.keys().next().value);
      tilesCinza.set(url, new Promise((resolve) => {
        const imagem = new Image();
        imagem.crossOrigin = 'anonymous'; // o INPE libera CORS; sem isso o canvas não pode ser lido
        imagem.onload = () => {
          const canvas = document.createElement('canvas');
          canvas.width = canvas.height = TILE;
          const ctx = canvas.getContext('2d', { willReadFrequently: true });
          ctx.drawImage(imagem, 0, 0, TILE, TILE);
          resolve(ctx.getImageData(0, 0, TILE, TILE).data);
        };
        imagem.onerror = () => { // tile ausente (ou o INPE ainda gravando a pasta): tenta de novo depois
          tilesCinza.delete(url);
          resolve(null);
        };
        imagem.src = url;
      }));
    }
    return tilesCinza.get(url);
  }

  function pintarCor(destino, i, cinza, alfa) {
    if (alfa === 0) return; // fora da cobertura do satélite
    const origem = cinza * 4;
    destino[i] = COR_POR_CINZA[origem];
    destino[i + 1] = COR_POR_CINZA[origem + 1];
    destino[i + 2] = COR_POR_CINZA[origem + 2];
    destino[i + 3] = COR_POR_CINZA[origem + 3];
  }

  function recolorir(cinza) {
    const quadro = new ImageData(TILE, TILE);
    for (let i = 0; i < cinza.length; i += 4) pintarCor(quadro.data, i, cinza[i], cinza[i + 3]);
    return quadro;
  }

  // Deslocamento em pixels do tile por minuto (dx para leste, dy para baixo) nos pontos
  // de uma malha a cada MALHA_VENTO px, com o vento interpolado na grade do Eta.
  function deslocamentosPorMinuto(coords, grade) {
    const lado = TILE / MALHA_VENTO + 1;
    const dx = new Float32Array(lado * lado);
    const dy = new Float32Array(lado * lado);
    for (let i = 0; i < lado; i++) {
      for (let j = 0; j < lado; j++) {
        const ponto = mapa.unproject(
          [coords.x * TILE + j * MALHA_VENTO, coords.y * TILE + i * MALHA_VENTO], coords.z,
        );
        const vento = ventoNoPonto(grade, ponto.lat, ponto.lng);
        if (!vento) continue; // fora da grade do Eta: a nuvem fica parada
        const metrosPorPixel = (40075016.686 * Math.cos((ponto.lat * Math.PI) / 180)) / (TILE * 2 ** coords.z);
        dx[i * lado + j] = (vento.u * 60) / metrosPorPixel;
        dy[i * lado + j] = (-vento.v * 60) / metrosPorPixel;
      }
    }
    return { lado, dx, dy };
  }

  // Quadro projetado `minutos` à frente: cada pixel busca a cor de onde a nuvem estava,
  // no mosaico 3×3 de tiles em volta (vizinhos[1][1] é o próprio tile).
  function projetar(vizinhos, desloc, minutos) {
    const quadro = new ImageData(TILE, TILE);
    const { lado, dx, dy } = desloc;
    for (let y = 0; y < TILE; y++) {
      const i = Math.min(Math.floor(y / MALHA_VENTO), lado - 2);
      const fy = (y - i * MALHA_VENTO) / MALHA_VENTO;
      for (let x = 0; x < TILE; x++) {
        const j = Math.min(Math.floor(x / MALHA_VENTO), lado - 2);
        const fx = (x - j * MALHA_VENTO) / MALHA_VENTO;
        const a = i * lado + j;
        const b = a + lado;
        const mx = (dx[a] * (1 - fx) + dx[a + 1] * fx) * (1 - fy) + (dx[b] * (1 - fx) + dx[b + 1] * fx) * fy;
        const my = (dy[a] * (1 - fx) + dy[a + 1] * fx) * (1 - fy) + (dy[b] * (1 - fx) + dy[b + 1] * fx) * fy;
        const sx = Math.round(x - mx * minutos) + TILE;
        const sy = Math.round(y - my * minutos) + TILE;
        if (sx < 0 || sy < 0 || sx >= 3 * TILE || sy >= 3 * TILE) continue;
        const origem = vizinhos[Math.floor(sy / TILE)][Math.floor(sx / TILE)];
        if (!origem) continue;
        const k = ((sy % TILE) * TILE + (sx % TILE)) * 4;
        pintarCor(quadro.data, (y * TILE + x) * 4, origem[k], origem[k + 3]);
      }
    }
    return quadro;
  }

  const CamadaNuvens = L.GridLayer.extend({
    initialize(opcoes) {
      this._quadros = [];
      this._indice = 0;
      this._grade = null;
      L.GridLayer.prototype.initialize.call(this, opcoes);
    },
    // quadros: [{horario, minutos, real}]; grade: vento em 500 hPa (para os projetados)
    definirQuadros(quadros, grade, indice) {
      this._quadros = quadros;
      this._grade = grade;
      this._indice = indice;
      this._incompleta = false;
      if (this._map) this.redraw();
    },
    mostrar(indice) {
      this._indice = indice;
      for (const chave in this._tiles) this._pintar(this._tiles[chave].el);
    },
    _pintar(canvas) {
      const quadro = canvas._quadros[this._indice] || canvas._quadros[canvas._indiceAgora] ||
        canvas._quadros.slice(0, canvas._indiceAgora).findLast(Boolean); // sem o "agora", o real mais recente
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, TILE, TILE);
      if (quadro) ctx.putImageData(quadro, 0, 0);
    },
    _url(horario, x, y, z) {
      return this.options.tiles
        .replace('{horario}', horario)
        .replace('{z}', z).replace('{x}', x).replace('{y}', (1 << z) - 1 - y); // os tiles do INPE são TMS
    },
    createTile(coords, pronto) {
      const canvas = L.DomUtil.create('canvas', 'leaflet-tile');
      canvas.width = canvas.height = TILE;
      canvas._quadros = new Array(this._quadros.length).fill(null);
      canvas._indiceAgora = this._quadros.findIndex((q) => q.minutos === 0);
      const guardar = (indice, quadro) => {
        canvas._quadros[indice] = quadro;
        if (indice === this._indice || indice === canvas._indiceAgora) this._pintar(canvas);
      };
      const agora = this._quadros[canvas._indiceAgora];
      this._quadros.forEach((q, indice) => {
        if (!q.real || q === agora) return;
        pixelsDoTile(this._url(q.horario, coords.x, coords.y, coords.z))
          .then((cinza) => cinza && guardar(indice, recolorir(cinza)));
      });
      if (!agora) {
        pronto(null, canvas);
        return canvas;
      }
      const projetados = this._quadros.map((q, indice) => [q, indice]).filter(([q]) => !q.real);
      const grade = this._grade;
      const linhas = [-1, 0, 1].map((di) => [-1, 0, 1].map((dj) => {
        // só o próprio tile é preciso sem projeção; os vizinhos dão de onde a nuvem vem
        if ((di || dj) && !projetados.length) return Promise.resolve(null);
        const vizinho = L.point(coords.x + dj, coords.y + di);
        vizinho.z = coords.z;
        if (!this._isValidTile(vizinho)) return Promise.resolve(null); // fora da cobertura: o INPE não tem
        return pixelsDoTile(this._url(agora.horario, vizinho.x, vizinho.y, coords.z));
      }));
      Promise.all(linhas.map((linha) => Promise.all(linha))).then((vizinhos) => {
        pronto(null, canvas);
        if (!vizinhos[1][1]) {
          this._incompleta = true; // a próxima atualização redesenha
          return;
        }
        guardar(canvas._indiceAgora, recolorir(vizinhos[1][1]));
        if (!projetados.length || !grade) return;
        const desloc = deslocamentosPorMinuto(coords, grade);
        for (const [q, indice] of projetados) guardar(indice, projetar(vizinhos, desloc, q.minutos));
      });
      return canvas;
    },
  });

  const elNuvens = document.getElementById('nuvens');
  const elContagemNuvens = document.getElementById('contagem-nuvens');
  const elLegendaNuvens = document.getElementById('legenda-nuvens');
  const elDicaNuvens = document.getElementById('dica-nuvens');
  const elAnimacaoNuvens = document.getElementById('animacao-nuvens');
  const elQuadroNuvens = document.getElementById('quadro-nuvens');
  const elTipoQuadroNuvens = document.getElementById('tipo-quadro-nuvens');
  const elPausarNuvens = document.getElementById('pausar-nuvens');
  const elSliderNuvens = document.getElementById('slider-nuvens');
  const formatoHora = new Intl.DateTimeFormat('pt-BR', {
    hour: '2-digit', minute: '2-digit', timeZone: 'America/Sao_Paulo',
  });
  let camadaNuvens = null;
  let quadrosNuvens = [];
  let indiceNuvens = 0;
  let horarioNuvens = null;
  let pausaNuvens = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let relogioNuvens = null;

  const horarioDoInpe = (data) => data.toISOString().replace(/\D/g, '').slice(0, 12); // AAAAMMDDhhmm (UTC)

  function mostrarQuadroNuvens(indice) {
    indiceNuvens = indice;
    const q = quadrosNuvens[indice];
    if (!q) return;
    camadaNuvens?.mostrar(indice);
    elSliderNuvens.value = indice;
    elQuadroNuvens.textContent = q.minutos === 0 ? 'agora' : `${q.minutos < 0 ? '−' : '+'}${Math.abs(q.minutos)} min`;
    const selo = document.createElement('strong');
    selo.className = 'selo-projecao';
    selo.textContent = 'Projeção';
    elTipoQuadroNuvens.replaceChildren(
      `${formatoHora.format(q.quando)} · `,
      q.real ? (q.minutos === 0 ? 'última imagem' : 'satélite') : selo,
    );
  }

  function agendarQuadroNuvens() {
    clearTimeout(relogioNuvens);
    elPausarNuvens.textContent = pausaNuvens ? '▶ Animar' : '⏸ Pausar';
    if (pausaNuvens || !camadaNuvens || quadrosNuvens.length < 2) return;
    const q = quadrosNuvens[indiceNuvens];
    const ultimo = indiceNuvens === quadrosNuvens.length - 1;
    relogioNuvens = setTimeout(() => {
      mostrarQuadroNuvens((indiceNuvens + 1) % quadrosNuvens.length);
      agendarQuadroNuvens();
    }, q.minutos === 0 || ultimo ? 1500 : 700); // segura um pouco no "agora" e no fim
  }

  async function ventoEmAltitude() {
    try {
      const resposta = await fetch(`${api.vento}?camada=500hPa`);
      return resposta.ok ? await resposta.json() : null;
    } catch {
      return null;
    }
  }

  async function carregarNuvens() {
    elLegendaNuvens.hidden = elDicaNuvens.hidden = elAnimacaoNuvens.hidden = !elNuvens.checked;
    if (!elNuvens.checked) {
      clearTimeout(relogioNuvens);
      if (camadaNuvens) mapa.removeLayer(camadaNuvens);
      camadaNuvens = null;
      horarioNuvens = null;
      tilesCinza.clear();
      elContagemNuvens.textContent = '';
      return;
    }
    if (!camadaNuvens) elContagemNuvens.textContent = 'carregando…';
    try {
      const resposta = await fetch(api.satelite);
      const info = await resposta.json();
      if (!resposta.ok) throw new Error(info.erro || `HTTP ${resposta.status}`);
      if (!elNuvens.checked) return;
      const quando = new Date(info.atual);
      let semVento = false;
      if (info.horario !== horarioNuvens) {
        const grade = await ventoEmAltitude();
        if (!elNuvens.checked) return;
        semVento = !grade;
        const disponiveis = new Set(info.horarios);
        quadrosNuvens = [];
        for (let k = -QUADROS_ANTES; k <= QUADROS_DEPOIS; k++) {
          const minutos = k * PASSO_QUADROS_MIN;
          const momento = new Date(quando.getTime() + minutos * 60000);
          if (k < 0 && disponiveis.has(horarioDoInpe(momento))) {
            quadrosNuvens.push({ horario: horarioDoInpe(momento), minutos, real: true, quando: momento });
          } else if (k === 0) {
            quadrosNuvens.push({ horario: info.horario, minutos, real: true, quando: momento });
          } else if (k > 0 && grade) {
            quadrosNuvens.push({ horario: info.horario, minutos, real: false, quando: momento });
          }
        }
        const indiceAgora = quadrosNuvens.findIndex((q) => q.minutos === 0);
        if (!camadaNuvens) {
          camadaNuvens = new CamadaNuvens({
            pane: 'nuvens',
            tiles: info.tiles,
            maxNativeZoom: info.zoom_maximo,
            bounds: L.latLngBounds(info.limites),
            attribution: 'Satélite GOES-19: INPE/CPTEC · vento em 500 hPa: Eta/CPTEC',
          });
          camadaNuvens.definirQuadros(quadrosNuvens, grade, indiceAgora);
          camadaNuvens.addTo(mapa);
        } else {
          camadaNuvens.definirQuadros(quadrosNuvens, grade, indiceAgora);
        }
        horarioNuvens = info.horario;
        elSliderNuvens.max = quadrosNuvens.length - 1;
        mostrarQuadroNuvens(indiceAgora);
        agendarQuadroNuvens();
      } else if (camadaNuvens._incompleta) {
        camadaNuvens._incompleta = false;
        camadaNuvens.redraw();
      }
      const minutos = Math.max(0, Math.round((Date.now() - quando) / 60000));
      elContagemNuvens.textContent = `última imagem das ${formatoHora.format(quando)} (Brasília), há ${minutos} min` +
        (semVento ? ' · projeção indisponível no momento' : '');
    } catch (erro) {
      elContagemNuvens.textContent = `erro ao carregar (${erro.message})`;
    }
  }

  elNuvens.addEventListener('change', carregarNuvens);
  elPausarNuvens.addEventListener('click', () => {
    pausaNuvens = !pausaNuvens;
    agendarQuadroNuvens();
  });
  elSliderNuvens.addEventListener('input', () => {
    pausaNuvens = true;
    mostrarQuadroNuvens(Number(elSliderNuvens.value));
    agendarQuadroNuvens();
  });
  setInterval(() => elNuvens.checked && carregarNuvens(), 5 * 60 * 1000); // o INPE publica a cada 10 min

  // ---- Vento a 10 m (previsão horária do modelo Eta, CPTEC) ---------------
  // Setas desenhadas num canvas a cada ESPACO_SETAS pixels da tela, em qualquer
  // zoom, com u e v interpolados na grade de 40 km. A seta aponta para onde o
  // vento vai; comprimento e cor crescem com a velocidade.
  mapa.createPane('vento').style.zIndex = 380; // acima das nuvens, abaixo dos pontos e polígonos
  mapa.getPane('vento').style.pointerEvents = 'none';
  const CLASSES_VENTO = JSON.parse(document.getElementById('classes-vento').textContent);
  const ESPACO_SETAS = 44;
  const PONTOS_CARDEAIS = ['N', 'NE', 'L', 'SE', 'S', 'SO', 'O', 'NO'];

  function ventoNoPonto(grade, lat, lon) {
    const y = (lat - grade.lat_sul) / grade.passo;
    const x = (lon - grade.lon_oeste) / grade.passo;
    if (y < 0 || x < 0 || y > grade.linhas - 1 || x > grade.colunas - 1) return null;
    const i = Math.min(Math.floor(y), grade.linhas - 2);
    const j = Math.min(Math.floor(x), grade.colunas - 2);
    const fy = y - i;
    const fx = x - j;
    const bilinear = (c) =>
      c[i][j] * (1 - fx) * (1 - fy) + c[i][j + 1] * fx * (1 - fy) + c[i + 1][j] * (1 - fx) * fy + c[i + 1][j + 1] * fx * fy;
    const u = bilinear(grade.u);
    const v = bilinear(grade.v);
    const de = ((Math.atan2(-u, -v) * 180) / Math.PI + 360) % 360; // de onde vem, a partir do norte
    return { u, v, kmh: Math.hypot(u, v) * 3.6, de, cardeal: PONTOS_CARDEAIS[Math.round(de / 45) % 8] };
  }

  function corDoVento(kmh) {
    let cor = CLASSES_VENTO[0].cor;
    for (const c of CLASSES_VENTO) if (kmh >= c.kmh) cor = c.cor;
    return cor;
  }

  function desenharSeta(ctx, x, y, vento) {
    const comprimento = Math.min(ESPACO_SETAS * 0.85, 10 + vento.kmh * 0.75);
    const angulo = Math.atan2(-vento.v, vento.u); // na tela o y cresce para baixo
    const dx = (Math.cos(angulo) * comprimento) / 2;
    const dy = (Math.sin(angulo) * comprimento) / 2;
    const ponta = 5;
    const tracar = () => {
      ctx.beginPath();
      ctx.moveTo(x - dx, y - dy);
      ctx.lineTo(x + dx, y + dy);
      ctx.moveTo(x + dx - ponta * Math.cos(angulo - 0.5), y + dy - ponta * Math.sin(angulo - 0.5));
      ctx.lineTo(x + dx, y + dy);
      ctx.lineTo(x + dx - ponta * Math.cos(angulo + 0.5), y + dy - ponta * Math.sin(angulo + 0.5));
      ctx.stroke();
    };
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.85)'; // contorno para destacar sobre qualquer fundo
    ctx.lineWidth = 4;
    tracar();
    ctx.strokeStyle = corDoVento(vento.kmh);
    ctx.lineWidth = 2;
    tracar();
  }

  const CamadaVento = L.Layer.extend({
    initialize(grade) {
      this._grade = grade;
    },
    definirGrade(grade) {
      this._grade = grade;
      this._desenhar();
    },
    onAdd(map) {
      this._canvas = L.DomUtil.create('canvas', 'leaflet-zoom-hide'); // some durante a animação de zoom
      map.getPane('vento').appendChild(this._canvas);
      map.on('moveend resize', this._desenhar, this);
      this._desenhar();
    },
    onRemove(map) {
      map.off('moveend resize', this._desenhar, this);
      this._canvas.remove();
    },
    _desenhar() {
      const map = this._map;
      if (!map) return;
      const tamanho = map.getSize();
      const escala = window.devicePixelRatio || 1;
      const canvas = this._canvas;
      canvas.width = tamanho.x * escala;
      canvas.height = tamanho.y * escala;
      canvas.style.width = `${tamanho.x}px`;
      canvas.style.height = `${tamanho.y}px`;
      L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
      const ctx = canvas.getContext('2d');
      ctx.scale(escala, escala);
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      for (let y = ESPACO_SETAS / 2; y < tamanho.y; y += ESPACO_SETAS) {
        for (let x = ESPACO_SETAS / 2; x < tamanho.x; x += ESPACO_SETAS) {
          const { lat, lng } = map.containerPointToLatLng([x, y]);
          const vento = ventoNoPonto(this._grade, lat, lng);
          if (vento) desenharSeta(ctx, x, y, vento);
        }
      }
    },
  });

  const elVento = document.getElementById('vento');
  const elContagemVento = document.getElementById('contagem-vento');
  const elLegendaVento = document.getElementById('legenda-vento');
  let camadaVento = null;
  let gradeVento = null;

  async function carregarVento() {
    elLegendaVento.hidden = !elVento.checked;
    if (!elVento.checked) {
      if (camadaVento) mapa.removeLayer(camadaVento);
      camadaVento = null;
      elContagemVento.textContent = '';
      return;
    }
    if (!camadaVento) elContagemVento.textContent = 'carregando…';
    try {
      const resposta = await fetch(api.vento);
      const dados = await resposta.json();
      if (!resposta.ok) throw new Error(dados.erro || `HTTP ${resposta.status}`);
      gradeVento = dados;
      if (!elVento.checked) return;
      if (camadaVento) camadaVento.definirGrade(dados);
      else camadaVento = new CamadaVento(dados).addTo(mapa);
      const rodada = new Date(dados.rodada);
      elContagemVento.textContent =
        `previsão para as ${formatoHora.format(new Date(dados.valido_em))} (Brasília) · ` +
        `rodada de ${formatoDia.format(rodada)}, ${String(rodada.getUTCHours()).padStart(2, '0')}h UTC`;
    } catch (erro) {
      elContagemVento.textContent = `erro ao carregar (${erro.message})`;
    }
  }

  elVento.addEventListener('change', carregarVento);
  setInterval(() => elVento.checked && carregarVento(), 10 * 60 * 1000); // muda a hora cheia

  // ---- Enchente do Guaíba (simulação) --------------------------------------
  // O comando preparar_enchente grava, por célula de ~85 m, o terreno e o "alcance":
  // o nível do Guaíba (dm) em que a água vinda do lago chega até ela. Mover o slider
  // é só comparar: alagada = alcance <= nível. A mancha observada por satélite em
  // 06/05/2024 entra como comparação, e os diques como linhas.
  mapa.createPane('enchente').style.zIndex = 355; // acima da chuva, abaixo das nuvens
  const elEnchente = document.getElementById('enchente');
  const elSimulador = document.getElementById('simulador-enchente');
  const elSliderEnchente = document.getElementById('slider-enchente');
  const elNivelEnchente = document.getElementById('nivel-enchente');
  const elAnimarEnchente = document.getElementById('animar-enchente');
  const elMarcasEnchente = document.getElementById('marcas-enchente');
  const elImpactoEnchente = document.getElementById('impacto-enchente');
  const elMunicipiosEnchente = document.getElementById('municipios-enchente');
  const elContagemEnchente = document.getElementById('contagem-enchente');
  const elComparar2024 = document.getElementById('comparar-2024');
  const elLegendaComparacao = document.getElementById('legenda-comparacao');
  const metros = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  // Profundidade (m) → cor; abaixo da faixa normal do rio, um azul bem claro.
  const CORES_PROFUNDIDADE = [
    [3, [11, 61, 145, 225]], [1.5, [28, 126, 214, 205]], [0.5, [77, 171, 247, 190]], [0, [165, 216, 255, 175]],
  ];
  const COR_VARZEA = [165, 216, 255, 70];
  let simulacao = null;
  let carregandoSimulacao = null;
  let quadroPendente = 0;
  let animacao = 0;

  async function prepararSimulacao() {
    const resposta = await fetch(api.enchente);
    const meta = await resposta.json();
    if (!resposta.ok) throw new Error(meta.erro || `HTTP ${resposta.status}`);
    const baixar = async (url, comoJson = false) => {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status} em ${url}`);
      return comoJson ? r.json() : r.arrayBuffer();
    };
    const { arquivos } = meta;
    const [bytesRelevo, bytesAlcance, bytesMunicipios, bytesObservado, diques] = await Promise.all([
      baixar(arquivos.relevo), baixar(arquivos.alcance), baixar(arquivos.municipios),
      baixar(arquivos.observado), baixar(arquivos.diques, true),
    ]);
    // Binários little-endian, como em todos os navegadores atuais.
    const relevo = new Int16Array(bytesRelevo);
    const observado = new Uint8Array(bytesObservado);
    const normalDm = Math.round(meta.nivel_normal * 10);
    let celulasObservadas = 0;
    for (let i = 0; i < relevo.length; i++) if (observado[i] && relevo[i] > normalDm) celulasObservadas++;
    const canvas = document.createElement('canvas');
    canvas.width = meta.colunas;
    canvas.height = meta.linhas;
    const ctx = canvas.getContext('2d');
    return {
      meta,
      relevo,
      municipios: new Uint8Array(bytesMunicipios),
      alcance: new Int16Array(bytesAlcance),
      observado,
      celulasObservadas,
      canvas,
      ctx,
      imagem: ctx.createImageData(meta.colunas, meta.linhas),
      camada: L.imageOverlay('data:,', meta.limites, { pane: 'enchente', interactive: false, opacity: 0.85 }),
      diques: L.geoJSON(diques, {
        style: { color: '#212529', weight: 3, opacity: 0.8 },
        bubblingMouseEvents: false,
        onEachFeature: (f, camada) => camada.bindTooltip(f.properties.nome, { sticky: true }),
      }),
    };
  }

  function desenharEnchente() {
    quadroPendente = 0;
    if (!simulacao) return;
    const { meta, relevo, alcance, municipios, observado, imagem } = simulacao;
    const comparar = elComparar2024.checked;
    const nivel = Number(elSliderEnchente.value);
    const nivelDm = Math.round(nivel * 10);
    const normalDm = Math.round(meta.nivel_normal * 10);
    const pixels = imagem.data;
    const celulasPorMunicipio = new Uint32Array(meta.municipios.length + 1);
    let celulasAlagadas = 0;
    for (let i = 0, p = 0; i < relevo.length; i++, p += 4) {
      const altura = relevo[i];
      if (altura <= 0) { // leito do lago e dos rios
        pixels[p + 3] = 0;
        continue;
      }
      if (alcance[i] > nivelDm) { // seco na simulação
        const soEm2024 = comparar && observado[i] && altura > normalDm;
        pixels[p] = 247;
        pixels[p + 1] = 103;
        pixels[p + 2] = 7;
        pixels[p + 3] = soEm2024 ? 200 : 0;
        continue;
      }
      let cor = COR_VARZEA;
      if (altura > normalDm) {
        const profundidade = (nivelDm - altura) / 10;
        cor = CORES_PROFUNDIDADE.find(([minimo]) => profundidade >= minimo)[1];
        celulasAlagadas++;
        celulasPorMunicipio[municipios[i]]++;
      }
      pixels[p] = cor[0];
      pixels[p + 1] = cor[1];
      pixels[p + 2] = cor[2];
      pixels[p + 3] = cor[3];
    }
    simulacao.ctx.putImageData(imagem, 0, 0);
    simulacao.camada.setUrl(simulacao.canvas.toDataURL());

    elNivelEnchente.value = `${metros.format(nivel)} m`;
    for (const botao of elMarcasEnchente.querySelectorAll('button')) {
      botao.classList.toggle('ativa', Math.abs(Number(botao.dataset.metros) - nivel) < 0.025);
    }
    const area = (celulas) => numero.format(Math.round(celulas * meta.area_celula_km2 * 10) / 10);
    elImpactoEnchente.replaceChildren();
    if (nivel <= meta.nivel_normal) {
      elImpactoEnchente.textContent = `Até ${metros.format(meta.nivel_normal)} m o Guaíba está na faixa normal.`;
    } else {
      const forte = document.createElement('strong');
      forte.textContent = `${area(celulasAlagadas)} km²`;
      elImpactoEnchente.append(forte, ` de terra alagada além do normal (acima de ${metros.format(meta.nivel_normal)} m).`);
    }
    if (comparar) {
      elImpactoEnchente.append(` Em 06/05/2024, com 5,37 m, o satélite mostrou ${area(simulacao.celulasObservadas)} km².`);
    }
    const ranking = meta.municipios
      .map((m, i) => ({ nome: m.nome, celulas: celulasPorMunicipio[i + 1] }))
      .filter((m) => m.celulas * meta.area_celula_km2 >= 0.5)
      .sort((a, b) => b.celulas - a.celulas)
      .slice(0, 6);
    elMunicipiosEnchente.replaceChildren(
      ...ranking.map((m) => {
        const li = document.createElement('li');
        li.textContent = `${m.nome}: ${area(m.celulas)} km²`;
        return li;
      }),
    );
  }

  function agendarDesenho() {
    if (!quadroPendente) quadroPendente = requestAnimationFrame(desenharEnchente);
  }

  function pararAnimacao() {
    cancelAnimationFrame(animacao);
    animacao = 0;
    elAnimarEnchente.textContent = '▶ Subir';
  }

  function animarEnchente() {
    if (animacao) {
      pararAnimacao();
      return;
    }
    const alvo = simulacao.meta.referencias.at(-1).metros; // até a cheia de 2024
    let atual = Number(elSliderEnchente.value);
    if (atual >= alvo - 0.01) atual = simulacao.meta.nivel_normal;
    elAnimarEnchente.textContent = '⏸ Parar';
    let anterior = performance.now();
    const passo = (agora) => {
      atual = Math.min(alvo, atual + ((agora - anterior) / 1000) * 0.8); // 0,8 m por segundo
      anterior = agora;
      elSliderEnchente.value = atual.toFixed(2);
      desenharEnchente();
      if (atual < alvo) animacao = requestAnimationFrame(passo);
      else pararAnimacao();
    };
    animacao = requestAnimationFrame(passo);
  }

  function montarMarcas(meta) {
    const marcas = [...meta.referencias];
    if (meta.nivel.atual) marcas.unshift({ metros: meta.nivel.atual.metros, nome: 'Hoje' });
    elMarcasEnchente.replaceChildren(
      ...marcas.map((m) => {
        const botao = document.createElement('button');
        botao.type = 'button';
        botao.dataset.metros = m.metros;
        botao.textContent = `${m.nome} · ${metros.format(m.metros)} m`;
        return botao;
      }),
    );
  }

  function desligarGuaiba() {
    pararAnimacao();
    if (simulacao) {
      mapa.removeLayer(simulacao.camada);
      mapa.removeLayer(simulacao.diques);
    }
  }

  async function ligarGuaiba() {
    try {
      if (!simulacao) {
        elContagemEnchente.textContent = 'carregando o relevo…';
        carregandoSimulacao ??= prepararSimulacao();
        simulacao = await carregandoSimulacao;
        const { meta } = simulacao;
        elSliderEnchente.max = meta.nivel_maximo;
        montarMarcas(meta);
        if (meta.nivel.atual) elSliderEnchente.value = meta.nivel.atual.metros;
      }
      if (!elEnchente.checked || elRegiaoEnchente.value !== 'guaiba') return;
      const { meta } = simulacao;
      simulacao.camada.addTo(mapa);
      simulacao.diques.addTo(mapa);
      mapa.fitBounds(meta.limites);
      const atual = meta.nivel.atual;
      elContagemEnchente.textContent = atual
        ? `Guaíba agora: ${metros.format(atual.metros)} m (${formatoData.format(new Date(atual.quando))}, ${meta.estacao.nome})`
        : `nível atual indisponível (${meta.nivel.erro})`;
      desenharEnchente();
    } catch (erro) {
      carregandoSimulacao = null;
      elContagemEnchente.textContent = `erro ao carregar (${erro.message})`;
    }
  }

  // ---- Belém: maré ao vivo, chuva e áreas de risco do SGB -----------------
  // Sem simulação de nível: o relevo disponível fica ~3 m alto demais nas baixadas.
  const elRegiaoEnchente = document.getElementById('regiao-enchente');
  const elPainelGuaiba = document.getElementById('painel-guaiba');
  const elPainelBelem = document.getElementById('painel-belem');
  const elMareAgora = document.getElementById('mare-agora');
  const elGraficoMare = document.getElementById('grafico-mare');
  const elMareAlta = document.getElementById('mare-alta');
  const elChuvaBelem = document.getElementById('chuva-belem');
  const elSetoresBelem = document.getElementById('setores-belem');
  const CORES_SETOR = { 'Inundação': '#1c7ed6', Alagamento: '#0c8599', 'Erosão': '#a0522d' };
  const formatoHoraMinuto = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit' });
  const formatoDiaHora = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  const relativo = (m) => `${m >= 0 ? '+' : '−'}${metros.format(Math.abs(m))} m`;
  let belem = null;
  let carregandoBelem = null;

  async function buscarBelem() {
    const resposta = await fetch(api.enchenteBelem);
    const dados = await resposta.json();
    if (!resposta.ok) throw new Error(dados.erro || `HTTP ${resposta.status}`);
    return dados;
  }

  async function prepararBelem() {
    const dados = await buscarBelem();
    const resposta = await fetch(dados.setores);
    if (!resposta.ok) throw new Error(`HTTP ${resposta.status} em ${dados.setores}`);
    const setores = await resposta.json();
    const camada = L.geoJSON(setores, {
      bubblingMouseEvents: false,
      style: (f) => {
        const cor = CORES_SETOR[f.properties.tipo] || '#495057';
        return { color: cor, weight: 1.5, fillColor: cor, fillOpacity: f.properties.grau === 'Muito Alto' ? 0.5 : 0.25 };
      },
      onEachFeature: (f, c) => {
        const s = f.properties;
        c.bindPopup(() =>
          linhas(`${s.tipo || 'Risco'} · risco ${(s.grau || '').toLowerCase()}`, [
            ['Local', s.local],
            ['Pessoas', s.pessoas ? numero.format(s.pessoas) : null],
            ['Edificações', s.edificacoes ? numero.format(s.edificacoes) : null],
            ['Mapeado em', s.data ? formatoDia.format(new Date(s.data)) : null],
            ['Descrição', s.descricao],
          ]),
          { maxWidth: 320 },
        );
      },
    });
    return { dados, setores, camada };
  }

  function desenharGraficoMare(mare) {
    const serie = mare.serie_48h.map((p) => [new Date(p.quando).getTime(), p.relativo]);
    const largura = 300;
    const altura = 96;
    const margem = { esquerda: 26, direita: 6, topo: 8, base: 14 };
    const t0 = serie[0][0];
    const t1 = serie.at(-1)[0];
    const yMin = Math.min(-2, ...serie.map((p) => p[1]));
    const yMax = Math.max(2, ...serie.map((p) => p[1]));
    const x = (tempo) => margem.esquerda + ((tempo - t0) / (t1 - t0 || 1)) * (largura - margem.esquerda - margem.direita);
    const y = (m) => margem.topo + ((yMax - m) / (yMax - yMin)) * (altura - margem.topo - margem.base);
    const ns = 'http://www.w3.org/2000/svg';
    const elemento = (tag, atributos, texto) => {
      const e = document.createElementNS(ns, tag);
      for (const [k, v] of Object.entries(atributos)) e.setAttribute(k, v);
      if (texto !== undefined) e.textContent = texto;
      return e;
    };
    const p90 = mare.mares_altas.p90;
    const filhos = [
      elemento('line', { x1: margem.esquerda, x2: largura - margem.direita, y1: y(0), y2: y(0), stroke: '#adb5bd', 'stroke-width': 1 }),
      elemento('text', { x: 2, y: y(0) + 3 }, 'média'),
      elemento('line', { x1: margem.esquerda, x2: largura - margem.direita, y1: y(p90), y2: y(p90), stroke: '#f76707', 'stroke-width': 1, 'stroke-dasharray': '4 3' }),
      elemento('text', { x: 2, y: y(p90) + 3, fill: '#c2410c' }, relativo(p90).replace(' m', '')),
      elemento('polyline', {
        points: serie.map(([tempo, m]) => `${x(tempo).toFixed(1)},${y(m).toFixed(1)}`).join(' '),
        fill: 'none', stroke: '#1c7ed6', 'stroke-width': 1.8, 'stroke-linejoin': 'round',
      }),
    ];
    for (const alta of mare.mares_altas.recentes) {
      const tempo = new Date(alta.quando).getTime();
      const acima = alta.relativo >= p90;
      filhos.push(elemento('circle', { cx: x(tempo), cy: y(alta.relativo), r: 2.8, fill: acima ? '#f76707' : '#0b3d91' }));
    }
    filhos.push(
      elemento('text', { x: margem.esquerda, y: altura - 3 }, formatoDiaHora.format(new Date(t0))),
      elemento('text', { x: largura - margem.direita, y: altura - 3, 'text-anchor': 'end' }, `agora (${formatoHoraMinuto.format(new Date(t1))})`),
    );
    elGraficoMare.replaceChildren(...filhos);
  }

  function desenharPainelBelem() {
    const { dados, setores } = belem;
    const { mare } = dados;
    if (mare.erro) {
      elMareAgora.textContent = `Maré indisponível agora (${mare.erro}).`;
      elGraficoMare.replaceChildren();
      elMareAlta.textContent = '';
    } else {
      const forte = document.createElement('strong');
      forte.textContent = relativo(mare.atual.relativo);
      elMareAgora.replaceChildren(
        'Maré agora: ', forte,
        ` em relação à média de 30 dias · ${mare.atual.tendencia} (${formatoHoraMinuto.format(new Date(mare.atual.quando))})`,
      );
      desenharGraficoMare(mare);
      const ultima = mare.mares_altas.recentes.at(-1);
      const acima = ultima && ultima.relativo >= mare.mares_altas.p90;
      elMareAlta.className = `mare-alta${acima ? ' acima' : ''}`;
      elMareAlta.textContent = ultima
        ? `Última maré alta confirmada: ${relativo(ultima.relativo)} em ${formatoDiaHora.format(new Date(ultima.quando))} — ` +
          (acima ? 'entre as 10% maiores dos últimos 30 dias' : 'dentro do normal') +
          ` (as 10% maiores passam de ${relativo(mare.mares_altas.p90)}; a maior foi ${relativo(mare.mares_altas.maxima)}).`
        : '';
    }
    const porTipo = {};
    for (const f of setores.features) {
      const s = f.properties;
      const total = (porTipo[s.tipo] ??= { setores: 0, pessoas: 0, edificacoes: 0 });
      total.setores++;
      total.pessoas += s.pessoas || 0;
      total.edificacoes += s.edificacoes || 0;
    }
    elSetoresBelem.replaceChildren(
      ...Object.entries(porTipo)
        .sort((a, b) => b[1].pessoas - a[1].pessoas)
        .map(([tipo, total]) => {
          const li = document.createElement('li');
          const cor = document.createElement('i');
          cor.style.background = CORES_SETOR[tipo] || '#495057';
          li.append(cor, `${tipo}: ${total.setores} setores · ${numero.format(total.pessoas)} pessoas · ` +
            `${numero.format(total.edificacoes)} edificações`);
          return li;
        }),
    );
  }

  async function carregarChuvaBelem() {
    const [lat, lon] = belem.dados.centro;
    try {
      const resposta = await fetch(`${api.chuvaNoPonto}?${new URLSearchParams({ lat, lon, dias: 1 })}`);
      const p = await resposta.json();
      elChuvaBelem.textContent = p.mm === null || p.mm === undefined
        ? 'Chuva em Belém: sem dado (rode python manage.py coletar --fonte chuva).'
        : `Chuva em Belém: ${numero.format(p.mm)} mm nas 24 h até 9h de ${formatoDia.format(new Date(p.fim))} (INPE/CPTEC MERGE).`;
    } catch {
      elChuvaBelem.textContent = '';
    }
  }

  function desligarBelem() {
    if (belem) mapa.removeLayer(belem.camada);
  }

  async function ligarBelem() {
    try {
      if (!belem) {
        elContagemEnchente.textContent = 'carregando a maré e as áreas de risco…';
        carregandoBelem ??= prepararBelem();
        belem = await carregandoBelem;
        desenharPainelBelem();
      }
      if (!elEnchente.checked || elRegiaoEnchente.value !== 'belem') return;
      belem.camada.addTo(mapa);
      mapa.fitBounds(belem.camada.getBounds(), { padding: [16, 16] });
      elContagemEnchente.textContent = `Belém: maré na estação ${belem.dados.estacao.nome}, a cada 15 min.`;
      carregarChuvaBelem();
    } catch (erro) {
      carregandoBelem = null;
      elContagemEnchente.textContent = `erro ao carregar (${erro.message})`;
    }
  }

  async function atualizarMareBelem() {
    if (!belem || !elEnchente.checked || elRegiaoEnchente.value !== 'belem') return;
    try {
      belem.dados = await buscarBelem();
      desenharPainelBelem();
    } catch { /* mantém a última leitura */ }
  }

  // ---- Rios perto do BC (Recife, BH, Rio, Curitiba): telemetria da ANA -------
  // O nível de agora contra o último ano da própria estação: acima do percentil 95 é cheia
  // (~18 dias por ano), acima do 99 é cheia rara (< 4 dias por ano). Não é cota oficial.
  const REGIOES_RIO = new Set(['recife', 'belo-horizonte', 'rio-de-janeiro', 'curitiba']);
  const elPainelRio = document.getElementById('painel-rio');
  const elRioAgora = document.getElementById('rio-agora');
  const elGraficoRio = document.getElementById('grafico-rio');
  const elRioReferencia = document.getElementById('rio-referencia');
  const elDicaRio = document.getElementById('dica-rio');
  const FAIXAS_RIO = [['p99', 'vermelho', 'cheia rara para o rio'], ['p95', 'amarelo', 'cheia'], ['', 'verde', 'normal']];
  let marcadorRio = null;
  let pedidoRio = 0;

  const faixaDoRio = (dados) => FAIXAS_RIO.find(([p]) => !p || dados.atual.metros >= dados.referencia[p]);

  function desenharGraficoRio({ serie, referencia: ref }) {
    const pontos = serie.map((p) => [new Date(p.quando).getTime(), p.metros]);
    const largura = 300;
    const altura = 104;
    const margem = { esquerda: 30, direita: 6, topo: 8, base: 14 };
    const t0 = pontos[0][0];
    const t1 = pontos.at(-1)[0];
    const valores = pontos.map((p) => p[1]);
    const yMin = Math.min(ref.p50, ...valores) - 0.1;
    const yMax = Math.max(ref.p99, ...valores) + 0.1;
    const x = (tempo) => margem.esquerda + ((tempo - t0) / (t1 - t0 || 1)) * (largura - margem.esquerda - margem.direita);
    const y = (m) => margem.topo + ((yMax - m) / (yMax - yMin)) * (altura - margem.topo - margem.base);
    const ns = 'http://www.w3.org/2000/svg';
    const elemento = (tag, atributos, texto) => {
      const e = document.createElementNS(ns, tag);
      for (const [k, v] of Object.entries(atributos)) e.setAttribute(k, v);
      if (texto !== undefined) e.textContent = texto;
      return e;
    };
    const linha = (m, cor, tracejado) => [
      elemento('line', {
        x1: margem.esquerda, x2: largura - margem.direita, y1: y(m), y2: y(m), stroke: cor, 'stroke-width': 1,
        ...(tracejado ? { 'stroke-dasharray': '4 3' } : {}),
      }),
      elemento('text', { x: 2, y: y(m) + 3, fill: cor }, metros.format(m)),
    ];
    elGraficoRio.replaceChildren(
      // faixa de cheia (p95 a p99) e de cheia rara (acima do p99)
      elemento('rect', { x: margem.esquerda, width: largura - margem.esquerda - margem.direita, y: y(yMax), height: y(ref.p99) - y(yMax), fill: '#fff5f5' }),
      elemento('rect', { x: margem.esquerda, width: largura - margem.esquerda - margem.direita, y: y(ref.p99), height: y(ref.p95) - y(ref.p99), fill: '#fff4e6' }),
      ...linha(ref.p50, '#868e96'),
      ...linha(ref.p95, '#e8590c', true),
      ...linha(ref.p99, '#c92a2a', true),
      elemento('polyline', {
        points: pontos.map(([tempo, m]) => `${x(tempo).toFixed(1)},${y(m).toFixed(1)}`).join(' '),
        fill: 'none', stroke: '#1c7ed6', 'stroke-width': 1.8, 'stroke-linejoin': 'round',
      }),
      elemento('circle', { cx: x(t1), cy: y(pontos.at(-1)[1]), r: 3, fill: '#0b3d91' }),
      elemento('text', { x: margem.esquerda, y: altura - 3 }, formatoDia.format(new Date(t0))),
      elemento('text', { x: largura - margem.direita, y: altura - 3, 'text-anchor': 'end' }, `agora (${formatoHoraMinuto.format(new Date(t1))})`),
    );
  }

  function desenharPainelRio(dados) {
    const { estacao, atual, variacao_6h: variacao, referencia: ref } = dados;
    const [, nivel, nome] = faixaDoRio(dados);
    const forte = document.createElement('strong');
    forte.className = `nivel-${nivel}`;
    forte.textContent = `${metros.format(atual.metros)} m · ${nome}`;
    const tendencia = variacao === null ? ''
      : Math.abs(variacao) < 0.05 ? ' · estável'
        : ` · ${variacao > 0 ? 'subiu' : 'desceu'} ${Math.round(Math.abs(variacao) * 100)} cm em 6 h`;
    elRioAgora.replaceChildren(`${estacao.rio} agora: `, forte, `${tendencia} (${formatoDiaHora.format(new Date(atual.quando))})`);
    desenharGraficoRio(dados);
    elRioReferencia.textContent =
      `No último ano: metade do tempo abaixo de ${metros.format(ref.p50)} m; acima de ${metros.format(ref.p95)} m só ` +
      `5% do tempo (~18 dias, faixa laranja) e acima de ${metros.format(ref.p99)} m, 1% (~4 dias, faixa vermelha). ` +
      `O máximo foi ${metros.format(ref.maximo)} m.`;
    elDicaRio.textContent =
      `Estação ${estacao.local} (ANA ${estacao.codigo}, ${estacao.operadora}): ${estacao.nota}. As faixas comparam com o ` +
      'último ano da própria estação, não são cotas oficiais de alerta da Defesa Civil.';
  }

  function desligarRio() {
    if (marcadorRio) mapa.removeLayer(marcadorRio);
    marcadorRio = null;
  }

  async function ligarRio(cidadeId, aproximar = true) {
    const pedido = ++pedidoRio;
    if (aproximar) elContagemEnchente.textContent = 'carregando o nível do rio…';
    try {
      const resposta = await fetch(`${api.rio}?${new URLSearchParams({ cidade: cidadeId })}`);
      const dados = await resposta.json();
      if (pedido !== pedidoRio || !elEnchente.checked || elRegiaoEnchente.value !== cidadeId) return;
      if (!resposta.ok) throw new Error(dados.erro || `HTTP ${resposta.status}`);
      desenharPainelRio(dados);
      const { estacao } = dados;
      const [, nivel, nome] = faixaDoRio(dados);
      desligarRio();
      marcadorRio = L.circleMarker([estacao.lat, estacao.lon], {
        radius: 9, color: '#fff', weight: 2.5, fillColor: NIVEIS[nivel].cor, fillOpacity: 1, bubblingMouseEvents: false,
      })
        .bindTooltip(`${estacao.rio} · ${metros.format(dados.atual.metros)} m (${nome})`, { direction: 'top' })
        .addTo(mapa);
      if (aproximar) {
        const { bc } = CIDADES.find((c) => c.id === cidadeId);
        mapa.fitBounds(L.latLngBounds([[estacao.lat, estacao.lon], [bc.lat, bc.lon]]), { padding: [70, 70], maxZoom: 12 });
      }
      elContagemEnchente.textContent = `${estacao.rio} em ${estacao.local}: leitura da ANA a cada 15 min.`;
    } catch (erro) {
      if (pedido === pedidoRio) elContagemEnchente.textContent = `erro ao carregar (${erro.message})`;
    }
  }

  function ligarEnchente() {
    elSimulador.hidden = !elEnchente.checked;
    const regiao = elRegiaoEnchente.value;
    elPainelGuaiba.hidden = regiao !== 'guaiba';
    elPainelBelem.hidden = regiao !== 'belem';
    elPainelRio.hidden = !REGIOES_RIO.has(regiao);
    if (!elEnchente.checked || regiao !== 'guaiba') desligarGuaiba();
    if (!elEnchente.checked || regiao !== 'belem') desligarBelem();
    if (!elEnchente.checked || !REGIOES_RIO.has(regiao)) desligarRio();
    if (!elEnchente.checked) return;
    if (regiao === 'guaiba') ligarGuaiba();
    else if (regiao === 'belem') ligarBelem();
    else ligarRio(regiao);
  }

  elRegiaoEnchente.addEventListener('change', ligarEnchente);
  setInterval(atualizarMareBelem, 10 * 60 * 1000); // a ANA publica a cada 15 min
  setInterval(() => elEnchente.checked && REGIOES_RIO.has(elRegiaoEnchente.value) && ligarRio(elRegiaoEnchente.value, false), 10 * 60 * 1000);

  elEnchente.addEventListener('change', ligarEnchente);
  elSliderEnchente.addEventListener('input', () => {
    pararAnimacao();
    agendarDesenho();
  });
  elAnimarEnchente.addEventListener('click', animarEnchente);
  elComparar2024.addEventListener('change', () => {
    elLegendaComparacao.hidden = !elComparar2024.checked;
    agendarDesenho();
  });
  elMarcasEnchente.addEventListener('click', (evento) => {
    const botao = evento.target.closest('button[data-metros]');
    if (!botao) return;
    pararAnimacao();
    elSliderEnchente.value = botao.dataset.metros;
    desenharEnchente();
  });

  carregarVento();
  carregarNuvens();
  atualizarTudo();
})();
