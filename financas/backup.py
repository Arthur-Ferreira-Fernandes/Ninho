"""Cópias diárias do banco SQLite (usado pelo programa do computador).

Uma cópia por dia, feita com a API de backup do SQLite (segura mesmo com o banco em uso),
guardando as mais recentes. Opcionalmente, uma segunda cópia numa pasta extra, por
exemplo uma pasta sincronizada com a nuvem (Google Drive, Dropbox...).
"""

import sqlite3
from datetime import date
from pathlib import Path

PREFIXO = "ninho-"
MANTER = 30


def fazer_backup(banco, pasta, hoje=None, manter=MANTER):
    """Copia o banco para pasta/ninho-AAAA-MM-DD.sqlite3, se a cópia de hoje ainda não existe.

    Devolve o caminho da cópia nova (ou None se não havia o que copiar ou já existia).
    """
    banco, pasta = Path(banco), Path(pasta)
    if not banco.exists():
        return None
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"{PREFIXO}{(hoje or date.today()):%Y-%m-%d}.sqlite3"
    if destino.exists():
        return None
    temporario = destino.with_suffix(".tmp")
    origem = sqlite3.connect(banco)
    try:
        copia = sqlite3.connect(temporario)
        try:
            origem.backup(copia)
        finally:
            copia.close()
    finally:
        origem.close()
    temporario.replace(destino)   # só aparece com o nome final quando está completa
    _limpar(pasta, manter)
    return destino


def _limpar(pasta, manter):
    copias = sorted(pasta.glob(f"{PREFIXO}*.sqlite3"))
    for antiga in copias[:-manter]:
        antiga.unlink()


def pastas_de_backup(pasta_dados):
    """A pasta padrão (dentro dos dados) e, se configurada, a pasta extra.

    A pasta extra vem do arquivo `pasta_backup_extra` (uma linha com o caminho) na pasta dos dados.
    """
    pasta_dados = Path(pasta_dados)
    pastas = [pasta_dados / "backups"]
    config = pasta_dados / "pasta_backup_extra"
    if config.exists():
        extra = config.read_text().strip()
        if extra:
            pastas.append(Path(extra).expanduser())
    return pastas


def backup_diario(pasta_dados, hoje=None):
    """Faz a cópia do dia em todas as pastas configuradas; devolve as cópias criadas."""
    banco = Path(pasta_dados) / "db.sqlite3"
    criadas = []
    for pasta in pastas_de_backup(pasta_dados):
        try:
            copia = fazer_backup(banco, pasta, hoje)
        except OSError as erro:   # pasta extra fora do ar (pendrive, nuvem) não impede o app de abrir
            print(f"Não foi possível salvar o backup em {pasta}: {erro}")
            continue
        if copia:
            criadas.append(copia)
    return criadas
