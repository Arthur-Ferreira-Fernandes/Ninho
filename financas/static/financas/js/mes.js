// Gráficos da página do mês: SVG desenhado à mão, sem bibliotecas.
// Os valores também estão nas listas e na tabela da página (os gráficos não são o único caminho).
(function () {
  const fonte = document.getElementById("dados-grafico");
  if (!fonte) return;
  const DADOS = JSON.parse(fonte.textContent);
  if (!DADOS) return;

  const NS = "http://www.w3.org/2000/svg";
  const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  const compacto = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });
  const nomeMes = (iso) => {
    const [a, m] = iso.split("-").map(Number);
    const n = new Date(a, m - 1, 1).toLocaleDateString("pt-BR", { month: "short" }).replace(".", "");
    return n.charAt(0).toUpperCase() + n.slice(1);
  };
  const dataCurta = (iso) => iso.slice(8, 10) + "/" + iso.slice(5, 7);
  const cor = (nome) => getComputedStyle(document.documentElement).getPropertyValue(nome).trim();

  function el(tag, attrs, pai) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (pai) pai.appendChild(n);
    return n;
  }
  function texto(conteudo, attrs, pai) {
    const t = el("text", attrs, pai);
    t.textContent = conteudo;
    return t;
  }

  // escala "bonita": 0, 1 mil, 2 mil...
  function ticks(max, quantos) {
    if (max <= 0) return [0];
    const bruto = max / quantos;
    const mag = Math.pow(10, Math.floor(Math.log10(bruto)));
    const passo = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((p) => p >= bruto);
    const lista = [];
    for (let v = 0; v <= max + passo * 0.001; v += passo) lista.push(v);
    if (lista[lista.length - 1] < max) lista.push(lista[lista.length - 1] + passo);
    return lista;
  }
  const rotuloEixo = (v) => (v === 0 ? "0" : "R$ " + compacto.format(v));

  // retângulo com 4px arredondados só na ponta (a base fica reta, encostada no eixo)
  function barra(x, y, w, h, paraCima, pai, attrs) {
    const r = Math.min(4, w / 2, h);
    let d;
    if (h <= 0) return null;
    if (paraCima) {
      d = `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
    } else {
      d = `M${x},${y}V${y + h - r}Q${x},${y + h} ${x + r},${y + h}H${x + w - r}Q${x + w},${y + h} ${x + w},${y + h - r}V${y}Z`;
    }
    return el("path", Object.assign({ d }, attrs || {}), pai);
  }

  // ---------- tooltip único ----------
  const dica = document.getElementById("tooltip-grafico");
  function mostrarDica(titulo, linhas, x, y) {
    dica.replaceChildren();
    const t = document.createElement("div");
    t.className = "tg-titulo";
    t.textContent = titulo;
    dica.appendChild(t);
    linhas.forEach((l) => {
      const linha = document.createElement("div");
      linha.className = "tg-linha" + (l.classe ? " " + l.classe : "");
      if (l.cor) {
        const k = document.createElement("i");
        k.style.background = l.cor;
        linha.appendChild(k);
      }
      const v = document.createElement("b");
      v.textContent = l.valor;
      const n = document.createElement("span");
      n.textContent = l.nome;
      linha.append(v, n);
      dica.appendChild(linha);
    });
    dica.hidden = false;
    const larg = dica.offsetWidth, alt = dica.offsetHeight;
    let left = x + 14, top = y + 14;
    if (left + larg > window.innerWidth - 8) left = x - larg - 14;
    if (top + alt > window.innerHeight - 8) top = y - alt - 14;
    dica.style.left = Math.max(8, left) + "px";
    dica.style.top = Math.max(8, top) + "px";
  }
  // com o foco do teclado, a dica acompanha o elemento quando a página rola
  let reposicionar = null;
  const esconderDica = () => { dica.hidden = true; reposicionar = null; };
  function focar(alvo, mostrar) {
    alvo.addEventListener("focus", () => {
      reposicionar = () => mostrar(...posicaoDoElemento(alvo));
      reposicionar();
    });
    alvo.addEventListener("blur", esconderDica);
  }
  function posicaoDoElemento(n) {
    const r = n.getBoundingClientRect();
    return [r.left + r.width / 2, r.top + r.height / 2];
  }

  // ---------- 1. Acumulado: linhas em degrau ----------
  function acumulado(caixa) {
    const dias = DADOS.dias;
    const W = caixa.clientWidth, H = 260;
    const m = { t: 16, r: 16, b: 28, l: 56 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    let r = 0, g = 0, gi = 0;
    const pontos = dias.map((d) => {
      r += d.entradas; g += d.gasto; gi += d.gasto + d.investido;
      return { d, recebido: r, gasto: g, total: gi };
    });
    const temInvestido = pontos.length && pontos[pontos.length - 1].total > pontos[pontos.length - 1].gasto;
    const max = Math.max(1, ...pontos.map((p) => Math.max(p.recebido, p.total)));
    const escala = ticks(max, 4), topo = escala[escala.length - 1];
    const n = dias.length;
    const x = (i) => m.l + (n <= 1 ? iw / 2 : (i * iw) / (n - 1));
    const y = (v) => m.t + ih - (v / topo) * ih;

    const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "svg-grafico" });
    const grade = el("g", { class: "grade" }, svg);
    escala.forEach((v) => {
      el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }, grade);
      texto(rotuloEixo(v), { x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: "eixo" }, grade);
    });
    const passoX = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 44))));
    dias.forEach((d, i) => {
      if (i % passoX === 0 || i === n - 1)
        texto(String(d.dia), { x: x(i), y: H - 8, "text-anchor": "middle", class: "eixo" }, svg);
    });

    // previsão: faixa suave a partir de amanhã
    const iHoje = dias.findIndex((d) => d.previsto);
    if (iHoje > 0) {
      const x0 = (x(iHoje - 1) + x(iHoje)) / 2;
      el("rect", { x: x0, y: m.t, width: W - m.r - x0, height: ih, class: "faixa-previsao" }, svg);
      texto("previsão", { x: x0 + 6, y: m.t + 12, class: "eixo" }, svg);
    }

    function degrau(chave) {
      let d = "";
      pontos.forEach((p, i) => {
        d += i === 0 ? `M${x(i)},${y(p[chave])}` : `H${x(i)}V${y(p[chave])}`;
      });
      return d;
    }
    const series = [
      { chave: "recebido", nome: "Recebido acumulado", cor: cor("--serie-recebido") },
      { chave: "gasto", nome: "Gasto acumulado", cor: cor("--serie-gasto") },
    ];
    if (temInvestido) series.push({ chave: "total", nome: "Gasto + investido", cor: cor("--serie-investido") });
    // de trás para a frente: "gasto + investido" fica por baixo e não esconde o gasto
    [...series].reverse().forEach((s) => {
      el("path", { d: degrau(s.chave), fill: "none", stroke: s.cor, "stroke-width": 2,
                   "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    });
    // pontos no fim, com anel da cor da superfície
    const ultimo = pontos[n - 1];
    series.forEach((s) => {
      el("circle", { cx: x(n - 1), cy: y(ultimo[s.chave]), r: 4, fill: s.cor, class: "ponto" }, svg);
    });

    // mira: linha vertical que encaixa no dia mais próximo
    const mira = el("line", { y1: m.t, y2: m.t + ih, class: "mira", visibility: "hidden" }, svg);
    const marcadores = series.map((s) => el("circle", { r: 4, fill: s.cor, class: "ponto", visibility: "hidden" }, svg));
    const area = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent", tabindex: 0,
                              class: "area-interativa", "aria-label": "Use as setas para percorrer os dias" }, svg);
    let atual = iHoje > 0 ? iHoje - 1 : n - 1;
    function marcar(i, px, py) {
      atual = Math.max(0, Math.min(n - 1, i));
      const p = pontos[atual];
      mira.setAttribute("x1", x(atual)); mira.setAttribute("x2", x(atual));
      mira.setAttribute("visibility", "visible");
      series.forEach((s, k) => {
        marcadores[k].setAttribute("cx", x(atual)); marcadores[k].setAttribute("cy", y(p[s.chave]));
        marcadores[k].setAttribute("visibility", "visible");
      });
      const linhas = series.map((s) => ({ nome: s.nome, valor: brl.format(p[s.chave]), cor: s.cor }));
      const dif = p.recebido - p.gasto;
      linhas.push({ nome: dif < 0 ? "gasto além do recebido" : "recebido além do gasto",
                    valor: brl.format(Math.abs(dif)), classe: dif < 0 ? "neg" : "pos" });
      const titulo = "Até " + dataCurta(p.d.data) + (p.d.previsto ? " · previsão" : "");
      if (px === undefined) {
        const r = svg.getBoundingClientRect();
        px = r.left + x(atual); py = r.top + m.t + 20;
      }
      mostrarDica(titulo, linhas, px, py);
    }
    function soltar() {
      mira.setAttribute("visibility", "hidden");
      marcadores.forEach((c) => c.setAttribute("visibility", "hidden"));
      esconderDica();
    }
    area.addEventListener("pointermove", (e) => {
      const r = svg.getBoundingClientRect();
      const i = Math.round(((e.clientX - r.left - m.l) / iw) * (n - 1));
      marcar(i, e.clientX, e.clientY);
    });
    area.addEventListener("pointerleave", soltar);
    area.addEventListener("focus", () => { marcar(atual); reposicionar = () => marcar(atual); });
    area.addEventListener("blur", soltar);
    area.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") { marcar(atual + 1); e.preventDefault(); }
      if (e.key === "ArrowLeft") { marcar(atual - 1); e.preventDefault(); }
      if (e.key === "Escape") soltar();
    });
    caixa.replaceChildren(svg);
  }

  // ---------- 2. Dia a dia: colunas para cima (entradas) e para baixo (saídas) ----------
  function diario(caixa) {
    const dias = DADOS.dias;
    const W = caixa.clientWidth, H = 240;
    const m = { t: 12, r: 12, b: 26, l: 56 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const maxCima = Math.max(0, ...dias.map((d) => d.entradas));
    const maxBaixo = Math.max(0, ...dias.map((d) => d.gasto + d.investido));
    const total = (maxCima + maxBaixo) || 1;
    // a área se divide entre cima e baixo na proporção dos maiores valores (mínimo de 25% para cada lado)
    let fracCima = Math.min(0.75, Math.max(0.25, maxCima / total));
    if (!maxCima) fracCima = 0.12;
    if (!maxBaixo) fracCima = 0.88;
    const zero = m.t + ih * fracCima;
    const escalaCima = maxCima ? (zero - m.t) / maxCima : 0;
    const escalaBaixo = maxBaixo ? (m.t + ih - zero) / maxBaixo : 0;
    const n = dias.length, banda = iw / n;
    const largura = Math.max(3, Math.min(24, banda - 2));

    const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "svg-grafico" });
    const grade = el("g", { class: "grade" }, svg);
    if (maxCima) {
      texto(rotuloEixo(maxCima), { x: m.l - 8, y: m.t + 8, "text-anchor": "end", class: "eixo" }, grade);
      el("line", { x1: m.l, x2: W - m.r, y1: zero - maxCima * escalaCima, y2: zero - maxCima * escalaCima }, grade);
    }
    if (maxBaixo) {
      texto(rotuloEixo(maxBaixo), { x: m.l - 8, y: m.t + ih, "text-anchor": "end", class: "eixo" }, grade);
      el("line", { x1: m.l, x2: W - m.r, y1: zero + maxBaixo * escalaBaixo, y2: zero + maxBaixo * escalaBaixo }, grade);
    }
    texto("entra", { x: m.l - 8, y: zero - 6, "text-anchor": "end", class: "eixo" }, grade);
    texto("sai", { x: m.l - 8, y: zero + 14, "text-anchor": "end", class: "eixo" }, grade);
    el("line", { x1: m.l, x2: W - m.r, y1: zero, y2: zero, class: "linha-zero" }, svg);

    const cR = cor("--serie-recebido"), cG = cor("--serie-gasto"), cI = cor("--serie-investido");
    const passoX = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 30))));
    dias.forEach((d, i) => {
      const cx = m.l + banda * i + banda / 2, x0 = cx - largura / 2;
      const g = el("g", { class: "coluna" + (d.previsto ? " previsto" : ""), tabindex: 0,
                          "aria-label": `${dataCurta(d.data)}: entra ${brl.format(d.entradas)}, sai ${brl.format(d.gasto + d.investido)}` }, svg);
      el("rect", { x: m.l + banda * i, y: m.t, width: banda, height: ih, fill: "transparent" }, g);   // área de toque
      if (d.entradas) barra(x0, zero - d.entradas * escalaCima - 1, largura, d.entradas * escalaCima, true, g, { fill: cR });
      // saídas: gasto encostado no eixo, investido depois, com 2px de respiro entre os dois
      const hG = d.gasto * escalaBaixo, hI = d.investido * escalaBaixo;
      if (hG && hI) el("rect", { x: x0, y: zero + 1, width: largura, height: hG, fill: cG }, g);   // o investido fecha a ponta
      else if (hG) barra(x0, zero + 1, largura, hG, false, g, { fill: cG });
      if (hI) barra(x0, zero + 1 + hG + (hG ? 2 : 0), largura, hI, false, g, { fill: cI });
      if (i % passoX === 0 || i === n - 1)
        texto(String(d.dia), { x: cx, y: H - 8, "text-anchor": "middle", class: "eixo" }, svg);

      const linhas = [];
      d.itens.filter((it) => it.tipo === "entrada").forEach((it) => linhas.push({ nome: it.nome, valor: "+ " + brl.format(it.valor), cor: cR }));
      d.itens.filter((it) => it.tipo !== "entrada").sort((a, b) => b.valor - a.valor).forEach((it) =>
        linhas.push({ nome: it.nome, valor: "− " + brl.format(it.valor), cor: it.tipo === "investido" ? cI : cG }));
      const mostrar = (px, py) => mostrarDica(dataCurta(d.data) + (d.previsto ? " · previsto" : ""),
        linhas.length ? linhas.slice(0, 8).concat(linhas.length > 8 ? [{ nome: `e mais ${linhas.length - 8}`, valor: "…" }] : [])
                      : [{ nome: "nada neste dia", valor: "—" }], px, py);
      g.addEventListener("pointermove", (e) => mostrar(e.clientX, e.clientY));
      g.addEventListener("pointerleave", esconderDica);
      focar(g, mostrar);
    });
    caixa.replaceChildren(svg);
  }

  // ---------- 3. Mês a mês: colunas agrupadas recebido × gasto ----------
  function meses(caixa) {
    const lista = DADOS.meses;
    const W = caixa.clientWidth, H = 240;
    const m = { t: 14, r: 8, b: 30, l: 50 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const max = Math.max(1, ...lista.map((l) => Math.max(l.recebido, l.gasto)));
    const escala = ticks(max, 4), topo = escala[escala.length - 1];
    const y = (v) => m.t + ih - (v / topo) * ih;
    const banda = iw / lista.length;
    const larg = Math.max(4, Math.min(24, (banda - 10) / 2 - 1));

    const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "svg-grafico" });
    const grade = el("g", { class: "grade" }, svg);
    escala.forEach((v) => {
      el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }, grade);
      texto(rotuloEixo(v), { x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: "eixo" }, grade);
    });
    const cR = cor("--serie-recebido"), cG = cor("--serie-gasto"), cI = cor("--serie-investido");
    lista.forEach((l, i) => {
      const cx = m.l + banda * i + banda / 2;
      const g = el("a", { href: "?mes=" + l.mes, class: "coluna-mes" + (l.previsao ? " previsto" : "") + (l.aberto ? " aberto" : ""),
                          "aria-label": `${nomeMes(l.mes)}: recebido ${brl.format(l.recebido)}, gasto ${brl.format(l.gasto)}` }, svg);
      if (l.aberto) el("rect", { x: m.l + banda * i + 2, y: m.t - 6, width: banda - 4, height: ih + 6, rx: 6, class: "destaque-mes" }, g);
      el("rect", { x: m.l + banda * i, y: m.t, width: banda, height: ih + m.b, fill: "transparent" }, g);
      barra(cx - larg - 1, y(l.recebido), larg, ih + m.t - y(l.recebido), true, g, { fill: cR });
      barra(cx + 1, y(l.gasto), larg, ih + m.t - y(l.gasto), true, g, { fill: cG });
      texto(nomeMes(l.mes), { x: cx, y: H - 10, "text-anchor": "middle", class: "eixo" + (l.aberto ? " eixo-forte" : "") }, g);
      const mostrar = (px, py) => mostrarDica(nomeMes(l.mes) + "/" + l.mes.slice(2, 4) + (l.previsao ? " · previsão" : ""), [
        { nome: "recebido", valor: brl.format(l.recebido), cor: cR },
        { nome: "gasto", valor: brl.format(l.gasto), cor: cG },
        { nome: "investido", valor: brl.format(l.guardado), cor: cI },
        { nome: l.sobra < 0 ? "faltam" : "sobram", valor: brl.format(Math.abs(l.sobra)), classe: l.sobra < 0 ? "neg" : "pos" },
      ], px, py);
      g.addEventListener("pointermove", (e) => mostrar(e.clientX, e.clientY));
      g.addEventListener("pointerleave", esconderDica);
      focar(g, mostrar);
    });
    caixa.replaceChildren(svg);
  }

  const DESENHOS = { acumulado, diario, meses };
  const caixas = [...document.querySelectorAll("[data-grafico]")];
  function desenhar() {
    caixas.forEach((c) => { if (c.clientWidth) DESENHOS[c.dataset.grafico](c); });
  }
  let largurasAntes = caixas.map((c) => c.clientWidth);
  new ResizeObserver(() => {
    const agora = caixas.map((c) => c.clientWidth);
    if (agora.some((w, i) => w !== largurasAntes[i])) { largurasAntes = agora; desenhar(); }
  }).observe(document.querySelector(".graficos"));
  // o tema (claro/escuro) muda as cores: redesenha
  new MutationObserver(desenhar).observe(document.documentElement, { attributes: true, attributeFilter: ["data-tema"] });
  window.addEventListener("scroll", () => (reposicionar ? reposicionar() : esconderDica()), { passive: true });
  desenhar();
})();
