# Ninho

Controle das finanças da família: saldo esperado x real, dinheiro do dia a dia, contas fixas, cartões e investimentos.
Cada família tem seu espaço (multi-tenant), e as pessoas entram por convite.

## Publicar na internet

O app roda a partir do `Dockerfile` em qualquer serviço que aceite Docker e ofereça PostgreSQL.
O que ele precisa:

| Variável | O que é |
|---|---|
| `DATABASE_URL` | Endereço do PostgreSQL (o serviço fornece) |
| `DJANGO_SECRET_KEY` | Chave longa e aleatória; o app se recusa a subir sem ela |
| `DJANGO_HTTPS` | `1` em produção: força HTTPS, cookies seguros e HSTS |
| `DJANGO_ALLOWED_HOSTS` | Domínios do site, separados por vírgula (no Render e no Railway o endereço deles é detectado sozinho) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Os mesmos domínios com `https://`, se usar domínio próprio |
| `PORT`, `WEB_CONCURRENCY` | Porta e número de processos (normalmente a plataforma define) |

As migrações rodam sozinhas a cada publicação. A plataforma pode checar se o app está no ar em `/saude/`
(responde `ok` e testa o banco).

### Render

1. Suba o projeto para um repositório no GitHub.
2. No Render: **New → Blueprint** e escolha o repositório. O `render.yaml` cria o app e o PostgreSQL,
   gera a `DJANGO_SECRET_KEY` e liga o HTTPS.
3. Escolha os planos na tela e confirme. O endereço fica algo como `https://ninho-xxxx.onrender.com`.

### Railway

1. **New Project → Deploy from GitHub repo** e escolha o repositório (o `Dockerfile` é detectado).
2. **New → Database → PostgreSQL** no mesmo projeto.
3. No serviço do app, em **Variables**: `DATABASE_URL` = `${{Postgres.DATABASE_URL}}`,
   `DJANGO_SECRET_KEY` = uma chave aleatória e `DJANGO_HTTPS` = `1`.
4. Em **Settings → Networking**, gere um domínio público.

Gerar uma chave: `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`

### Domínio próprio

Aponte o domínio no painel da plataforma e acrescente-o em `DJANGO_ALLOWED_HOSTS`
(ex.: `ninho.com.br`) e em `DJANGO_CSRF_TRUSTED_ORIGINS` (ex.: `https://ninho.com.br`).

### Levar seus dados atuais para o servidor

No seu computador, exporte o que está no SQLite:

```sh
.venv/bin/python manage.py dumpdata --natural-foreign --natural-primary \
  -e contenttypes -e auth.Permission -e admin.LogEntry -e sessions -o dados.json
```

Depois, com a `DATABASE_URL` do servidor (o painel da plataforma mostra a versão de acesso externo):

```sh
DATABASE_URL="postgres://..." .venv/bin/python manage.py migrate
DATABASE_URL="postgres://..." .venv/bin/python manage.py loaddata dados.json
```

Logins e senhas vão junto. Apague o `dados.json` depois: ele tem todas as informações financeiras.

### Backup

Os planos pagos de PostgreSQL costumam ter backup automático; confira no painel. Para uma cópia manual:

```sh
pg_dump "postgres://..." > backup-$(date +%F).sql
```

## Usar como app no celular

O Ninho é um PWA: aberto pelo endereço com HTTPS, dá para instalar na tela inicial.

- **iPhone (Safari):** Compartilhar → Adicionar à Tela de Início.
- **Android (Chrome):** menu ⋮ → Instalar app. No Chrome, também aparece o botão **Instalar app** no topo.

O app guarda no aparelho só os arquivos visuais e uma página de "sem conexão"; as páginas com dados
financeiros sempre vêm do servidor.

## Rodando com Docker no computador

Sobe o app com PostgreSQL, como na hospedagem:

```sh
cp .env.example .env        # troque DJANGO_SECRET_KEY e POSTGRES_PASSWORD
docker compose up -d --build
```

Abra http://localhost:8002. Os dados ficam no volume `banco` (só `docker compose down -v` apaga).

```sh
docker compose logs -f web                                  # acompanhar o servidor
docker compose exec web python manage.py test financas      # testes contra o PostgreSQL
docker compose exec db pg_dump -U ninho ninho > backup.sql  # backup
```

## Desenvolvimento local

Sem `DATABASE_URL`, usa SQLite (`db.sqlite3`):

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver      # porta 8001
.venv/bin/python manage.py test financas
```

## Programa no computador (Linux)

Gera um executável único que sobe o Ninho e abre o navegador:

```sh
desktop/build.sh          # cria dist/ninho e o atalho "Ninho" no menu de aplicativos
./dist/ninho              # http://127.0.0.1:8003
```

Os dados ficam em `~/.local/share/ninho` (banco `db.sqlite3` e a chave secreta), separados do executável:
gerar uma versão nova não apaga nada. Fechar a janela do terminal encerra o programa.
