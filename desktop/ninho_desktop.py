"""Ninho como programa do computador: sobe o servidor local e abre o navegador.

Os dados ficam em ~/.local/share/ninho (banco SQLite e chave secreta), fora do executável,
então trocar o executável por uma versão nova não apaga nada.
"""

import os
import secrets
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# 8000 é do Master Obra, 8001 do runserver e 8002 do Docker
PORTA = int(os.environ.get("NINHO_PORTA", "8003"))
ENDERECO = f"http://127.0.0.1:{PORTA}/"
PASTA_DADOS = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ninho"


def _ja_esta_rodando():
    try:
        with urllib.request.urlopen(ENDERECO + "saude/", timeout=1) as resposta:
            return resposta.read() == b"ok"
    except OSError:
        return False


def _chave_secreta():
    arquivo = PASTA_DADOS / "chave_secreta"
    if not arquivo.exists():
        arquivo.write_text(secrets.token_urlsafe(50))
        arquivo.chmod(0o600)
    return arquivo.read_text().strip()


def _configurar_ambiente():
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    # no executável, o código e os estáticos coletados no build ficam em sys._MEIPASS
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(base))
    os.environ.update({
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DJANGO_DEBUG": "0",
        "DJANGO_SECRET_KEY": _chave_secreta(),
        "DJANGO_DB_PATH": str(PASTA_DADOS / "db.sqlite3"),
        "DJANGO_STATIC_ROOT": str(base / "staticfiles"),
        "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost",
        "DJANGO_CSRF_TRUSTED_ORIGINS": f"http://127.0.0.1:{PORTA},http://localhost:{PORTA}",
        "DJANGO_LOG_LEVEL": "WARNING",
    })
    os.environ.pop("DATABASE_URL", None)


def _abrir_navegador_quando_pronto():
    for _ in range(50):
        if _ja_esta_rodando():
            webbrowser.open(ENDERECO)
            return
        time.sleep(0.2)


def main():
    if _ja_esta_rodando():
        webbrowser.open(ENDERECO)
        return

    _configurar_ambiente()

    import django
    from django.core.management import call_command
    django.setup()
    call_command("migrate", interactive=False, verbosity=0)

    from waitress import serve
    from config.wsgi import application

    print(f"Ninho rodando em {ENDERECO}")
    print(f"Dados em {PASTA_DADOS}")
    print("Feche esta janela (ou Ctrl+C) para encerrar.")
    threading.Thread(target=_abrir_navegador_quando_pronto, daemon=True).start()
    serve(application, host="127.0.0.1", port=PORTA, threads=8, _quiet=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
