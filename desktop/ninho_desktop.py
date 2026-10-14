"""Ninho como programa do computador: sobe o servidor local e abre o navegador.

Os dados ficam em ~/.local/share/ninho (banco SQLite e chave secreta), fora do executável,
então trocar o executável por uma versão nova não apaga nada. Uma cópia do banco é feita
por dia em ~/.local/share/ninho/backups (veja financas/backup.py).

Com o Tailscale conectado, o Ninho também fica acessível (e instalável) em https://<computador>.ts.net
nos seus outros aparelhos (veja financas/tailscale.py e desktop/acesso_remoto.sh).
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
    """Prepara o Django; devolve o que o Tailscale informou (ou None)."""
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    # no executável, o código e os estáticos coletados no build ficam em sys._MEIPASS
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(base))
    hosts = ["127.0.0.1", "localhost"]
    origens = [f"http://127.0.0.1:{PORTA}", f"http://localhost:{PORTA}"]

    from financas.tailscale import detectar
    remoto = detectar(PORTA)
    if remoto:
        hosts.append(remoto["host"])
        origens.append(f"https://{remoto['host']}")
        if remoto["publicado"]:
            os.environ["NINHO_ENDERECO_REMOTO"] = f"https://{remoto['host']}/"

    os.environ.update({
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DJANGO_DEBUG": "0",
        "DJANGO_SECRET_KEY": _chave_secreta(),
        "DJANGO_DB_PATH": str(PASTA_DADOS / "db.sqlite3"),
        "DJANGO_STATIC_ROOT": str(base / "staticfiles"),
        "DJANGO_ALLOWED_HOSTS": ",".join(hosts),
        "DJANGO_CSRF_TRUSTED_ORIGINS": ",".join(origens),
        "DJANGO_LOG_LEVEL": "WARNING",
    })
    os.environ.pop("DATABASE_URL", None)
    return remoto


def _backups_diarios():
    """Uma cópia do banco por dia enquanto o programa fica aberto."""
    from financas.backup import backup_diario
    while True:
        time.sleep(60 * 60)
        backup_diario(PASTA_DADOS)


def _abrir_navegador_quando_pronto():
    for _ in range(50):
        if _ja_esta_rodando():
            webbrowser.open(ENDERECO)
            return
        time.sleep(0.2)


def main():
    sys.stdout.reconfigure(line_buffering=True)   # mensagens aparecem na hora, mesmo fora de um terminal
    if _ja_esta_rodando():
        webbrowser.open(ENDERECO)
        return

    remoto = _configurar_ambiente()

    # antes das migrações: uma versão nova nunca mexe no banco sem uma cópia do dia
    from financas.backup import backup_diario
    backup_diario(PASTA_DADOS)
    threading.Thread(target=_backups_diarios, daemon=True).start()

    import django
    from django.core.management import call_command
    django.setup()
    call_command("migrate", interactive=False, verbosity=0)

    from waitress import serve
    from config.wsgi import application

    print(f"Ninho rodando em {ENDERECO}")
    print(f"Dados em {PASTA_DADOS} (cópias diárias em {PASTA_DADOS / 'backups'})")
    if remoto and remoto["publicado"]:
        print(f"Nos outros aparelhos (com Tailscale): https://{remoto['host']}/")
    elif remoto:
        print(f"Tailscale conectado, mas o Ninho não foi publicado: {remoto['erro']}")
        print("Rode desktop/acesso_remoto.sh para concluir a configuração.")
    else:
        print("Para usar e instalar em outros aparelhos, configure o Tailscale: desktop/acesso_remoto.sh")
    print("Feche esta janela (ou Ctrl+C) para encerrar.")
    threading.Thread(target=_abrir_navegador_quando_pronto, daemon=True).start()
    serve(application, host="127.0.0.1", port=PORTA, threads=8, _quiet=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
