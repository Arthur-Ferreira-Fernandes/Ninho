"""Acesso pelos outros aparelhos com o Tailscale (usado pelo programa do computador).

O Ninho continua ouvindo só em 127.0.0.1. O `tailscale serve` publica esse endereço em HTTPS
(https://<computador>.<rede>.ts.net), visível só para os aparelhos da sua rede Tailscale. Com
HTTPS, o navegador deixa instalar o app. Configuração única: desktop/acesso_remoto.sh.
"""

import json
import shutil
import subprocess


def _rodar(*args, timeout=10):
    return subprocess.run(["tailscale", *args], capture_output=True, text=True, timeout=timeout)


def nome_do_computador(status_json):
    """O endereço deste computador na rede Tailscale (ex.: "casa.tail1234.ts.net"), ou None se desconectado."""
    try:
        dados = json.loads(status_json)
        nome = (dados.get("Self") or {}).get("DNSName", "").rstrip(".")
    except (ValueError, AttributeError):
        return None
    return nome if dados.get("BackendState") == "Running" and nome else None


def publicando(serve_json, porta):
    """O `tailscale serve` já encaminha para o Ninho nesta porta?"""
    return f"127.0.0.1:{porta}" in serve_json or f"localhost:{porta}" in serve_json


def detectar(porta):
    """{"host": ..., "publicado": bool, "erro": str} se o Tailscale está conectado; senão None.

    Tenta publicar o Ninho (`tailscale serve --bg`) se ainda não estiver publicado.
    """
    if not shutil.which("tailscale"):
        return None
    try:
        host = nome_do_computador(_rodar("status", "--json").stdout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not host:
        return None
    try:
        if publicando(_rodar("serve", "status", "--json").stdout, porta):
            return {"host": host, "publicado": True, "erro": ""}
        r = _rodar("serve", "--bg", str(porta), timeout=20)
    except subprocess.TimeoutExpired:
        # costuma ser o HTTPS ainda não liberado na conta: o comando fica esperando a confirmação
        return {"host": host, "publicado": False, "erro": "o tailscale serve não respondeu (HTTPS liberado na conta?)"}
    except OSError as e:
        return {"host": host, "publicado": False, "erro": str(e)}
    if r.returncode == 0:
        return {"host": host, "publicado": True, "erro": ""}
    return {"host": host, "publicado": False, "erro": (r.stderr or r.stdout).strip()}
