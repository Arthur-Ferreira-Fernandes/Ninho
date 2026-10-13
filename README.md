# Ninho

Controle das finanças da família: saldo esperado x saldo real, dinheiro do dia a dia, contas fixas, cartões e investimentos.

## Rodando com Docker

```sh
cp .env.example .env        # e troque DJANGO_SECRET_KEY por uma chave aleatória
docker compose up -d --build
```

Abra http://localhost:8002 (a porta muda em `NINHO_PORTA` no `.env`).

- O banco SQLite fica no volume `dados`, então sobrevive a `docker compose down` e a novas versões da imagem. Só `docker compose down -v` apaga os dados.
- As migrações rodam sozinhas a cada subida.
- Para acessar de outro aparelho da rede, inclua o IP do computador em `DJANGO_ALLOWED_HOSTS` e `DJANGO_CSRF_TRUSTED_ORIGINS`.

Comandos úteis:

```sh
docker compose logs -f web                          # acompanhar o servidor
docker compose exec web python manage.py createsuperuser
docker compose up -d --build                        # depois de alterar o código
```

### Levar os dados do desenvolvimento para o Docker

```sh
docker compose cp db.sqlite3 web:/app/dados/db.sqlite3
docker compose exec -u root web chown ninho:ninho /app/dados/db.sqlite3
docker compose restart web
```

### Backup

```sh
docker compose cp web:/app/dados/db.sqlite3 ./backup-$(date +%F).sqlite3
```

## Desenvolvimento local

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver      # porta 8001
.venv/bin/python manage.py test financas
```
