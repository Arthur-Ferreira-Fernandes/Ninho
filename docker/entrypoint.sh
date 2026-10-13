#!/bin/sh
set -e

# aplica as migrações pendentes a cada subida (não faz nada se já estiver em dia)
python manage.py migrate --noinput

exec "$@"
