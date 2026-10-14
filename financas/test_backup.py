import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

from django.test import SimpleTestCase

from .backup import backup_diario, fazer_backup


class BackupTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.banco = self.tmp / "db.sqlite3"
        with sqlite3.connect(self.banco) as c:
            c.execute("create table t (x)")
            c.execute("insert into t values (42)")

    def test_uma_copia_por_dia_e_legivel(self):
        copia = fazer_backup(self.banco, self.tmp / "backups", date(2026, 10, 14))
        self.assertEqual(copia.name, "ninho-2026-10-14.sqlite3")
        with sqlite3.connect(copia) as c:
            self.assertEqual(c.execute("select x from t").fetchone(), (42,))
        # no mesmo dia não copia de novo
        self.assertIsNone(fazer_backup(self.banco, self.tmp / "backups", date(2026, 10, 14)))
        self.assertFalse(list((self.tmp / "backups").glob("*.tmp")))

    def test_guarda_so_as_mais_recentes(self):
        inicio = date(2026, 1, 1)
        for i in range(5):
            fazer_backup(self.banco, self.tmp / "b", inicio + timedelta(days=i), manter=3)
        nomes = sorted(p.name for p in (self.tmp / "b").iterdir())
        self.assertEqual(nomes, ["ninho-2026-01-03.sqlite3", "ninho-2026-01-04.sqlite3", "ninho-2026-01-05.sqlite3"])

    def test_sem_banco_nao_faz_nada(self):
        self.assertIsNone(fazer_backup(self.tmp / "nao-existe.sqlite3", self.tmp / "b"))

    def test_pasta_extra_configurada(self):
        extra = self.tmp / "nuvem"
        (self.tmp / "pasta_backup_extra").write_text(f"{extra}\n")
        criadas = backup_diario(self.tmp, date(2026, 10, 14))
        self.assertEqual({p.parent for p in criadas}, {self.tmp / "backups", extra})

    def test_pasta_extra_inacessivel_nao_impede(self):
        (self.tmp / "bloqueio").write_text("arquivo, não pasta")
        (self.tmp / "pasta_backup_extra").write_text(str(self.tmp / "bloqueio" / "sub"))
        criadas = backup_diario(self.tmp, date(2026, 10, 14))
        self.assertEqual([p.parent for p in criadas], [self.tmp / "backups"])
