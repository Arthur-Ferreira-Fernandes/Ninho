// Service worker do {{ marca.nome }}.
// Guarda só arquivos visuais (CSS, ícones) e a página "sem conexão".
// As páginas com dados financeiros nunca ficam guardadas no aparelho.
const CACHE = "ninho-{{ versao|escapejs }}";
const OFFLINE = "/offline/";
const ARQUIVOS = [OFFLINE{% for a in arquivos %}, "{{ a|escapejs }}"{% endfor %}];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ARQUIVOS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((nomes) => Promise.all(nomes.filter((n) => n !== CACHE).map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // páginas: sempre da rede; sem conexão, a página de aviso
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match(OFFLINE)));
    return;
  }
  // arquivos estáticos (nomes mudam a cada versão): do cache, senão da rede
  if (url.pathname.startsWith("{{ prefixo_static|escapejs }}")) {
    e.respondWith(
      caches.match(req).then((guardado) => guardado || fetch(req).then((resp) => {
        if (resp.ok) {
          const copia = resp.clone();
          caches.open(CACHE).then((c) => c.put(req, copia));
        }
        return resp;
      }))
    );
  }
});
