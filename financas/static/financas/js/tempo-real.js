// Atualiza a tela sozinha quando a família muda algo em outro aparelho (ou em outra aba).
// Consulta /versao/ de tempos em tempos; quando muda, troca o <main> pelo da página atual,
// mantendo a rolagem e o que estava aberto. Se a pessoa estiver preenchendo um formulário,
// não troca nada: só avisa, e ela atualiza quando quiser.
(function () {
  const meta = document.querySelector('meta[name="ninho-versao"]');
  if (!meta || !window.fetch || !window.DOMParser) return;

  const INTERVALO = 5000;
  let versao = meta.content;
  let ocupado = false;
  let preenchendo = false;   // alguém digitou num formulário desta tela

  const principal = () => document.querySelector("main");
  const marcar = (e) => { if (e.target.closest && e.target.closest("main form")) preenchendo = true; };
  document.addEventListener("input", marcar);
  document.addEventListener("change", marcar);
  document.addEventListener("submit", () => { preenchendo = false; });

  function editando() {
    const ativo = document.activeElement;
    const campoEmUso = ativo && ativo.matches("input:not([type=checkbox]):not([type=radio]), textarea, select")
      && ativo.closest("main");
    return preenchendo || campoEmUso || document.querySelector("main dialog[open]");
  }

  // aviso fixo com botão para atualizar (usado quando não dá para trocar sozinho)
  function avisar() {
    let aviso = document.getElementById("aviso-mudancas");
    if (aviso) return;
    aviso = document.createElement("div");
    aviso.id = "aviso-mudancas";
    aviso.className = "aviso-tempo-real";
    aviso.setAttribute("role", "status");
    aviso.innerHTML = '<span>Há mudanças feitas em outro aparelho.</span>' +
      '<button type="button" class="btn">Atualizar</button>';
    aviso.querySelector("button").addEventListener("click", () => {
      preenchendo = false;
      aviso.remove();
      atualizar(true);
    });
    document.body.appendChild(aviso);
  }

  function notificar(texto) {
    const n = document.createElement("div");
    n.className = "aviso-tempo-real discreto";
    n.setAttribute("role", "status");
    n.textContent = texto;
    document.body.appendChild(n);
    setTimeout(() => n.classList.add("saindo"), 2200);
    setTimeout(() => n.remove(), 2800);
  }

  // scripts vindos do DOMParser não rodam: recria cada um para que rodem na página.
  // Os embutidos rodam dentro de um bloco { }: os const/let de nível superior da primeira
  // execução já existem, e declarar de novo derrubaria o script inteiro.
  function ativarScripts(raiz) {
    raiz.querySelectorAll("script").forEach((antigo) => {
      const novo = document.createElement("script");
      [...antigo.attributes].forEach((a) => novo.setAttribute(a.name, a.value));
      const executavel = !antigo.type || antigo.type === "text/javascript";
      novo.textContent = executavel && !antigo.src ? "{\n" + antigo.textContent + "\n}" : antigo.textContent;
      antigo.replaceWith(novo);
    });
  }

  async function atualizar(pedido) {
    let resposta;
    try {
      resposta = await fetch(location.href, { cache: "no-store", credentials: "same-origin" });
    } catch (e) {
      return;
    }
    // o item desta tela pode ter sido excluído, ou a sessão expirou: recarrega de verdade
    if (!resposta.ok || new URL(resposta.url).pathname !== location.pathname) {
      if (pedido) location.reload(); else avisar();
      return;
    }
    const doc = new DOMParser().parseFromString(await resposta.text(), "text/html");
    const novoMain = doc.querySelector("main");
    const atual = principal();
    if (!novoMain || !atual) return;

    const abertos = [...atual.querySelectorAll("details")].map((d) => d.open);
    const y = window.scrollY;
    atual.replaceWith(novoMain);
    const detalhes = [...novoMain.querySelectorAll("details")];
    if (detalhes.length === abertos.length) detalhes.forEach((d, i) => { d.open = abertos[i]; });
    ativarScripts(novoMain);
    window.scrollTo(0, y);

    const novaVersao = doc.querySelector('meta[name="ninho-versao"]');
    if (novaVersao) versao = novaVersao.content;
    document.title = doc.title;
    preenchendo = false;
    document.dispatchEvent(new CustomEvent("ninho:atualizado"));
    notificar("Atualizado com mudanças de outro aparelho");
  }

  async function verificar() {
    if (ocupado || document.hidden) return;
    ocupado = true;
    try {
      const r = await fetch("/versao/", { cache: "no-store", credentials: "same-origin",
                                          headers: { Accept: "application/json" } });
      if (!r.ok) return;
      const nova = String((await r.json()).versao);
      if (nova === versao) return;
      if (editando()) {
        avisar();
      } else {
        versao = nova;   // evita repetir enquanto a troca acontece
        await atualizar(false);
      }
    } catch (e) {
      // sem conexão: tenta de novo no próximo ciclo
    } finally {
      ocupado = false;
    }
  }

  setInterval(verificar, INTERVALO);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) verificar(); });
  window.addEventListener("focus", verificar);
  window.addEventListener("online", verificar);
})();
