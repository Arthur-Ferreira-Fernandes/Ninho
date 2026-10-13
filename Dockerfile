FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_DEBUG=0 \
    DJANGO_DB_PATH=/app/dados/db.sqlite3

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# arquivos estáticos já comprimidos na imagem; o whitenoise serve
RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput \
    && useradd --create-home --uid 1000 ninho \
    && mkdir -p /app/dados \
    && chown -R ninho:ninho /app/dados \
    && chmod +x docker/entrypoint.sh

USER ninho
VOLUME ["/app/dados"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/entrar/')" || exit 1

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
