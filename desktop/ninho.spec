# PyInstaller: gera dist/ninho (um único arquivo). Use desktop/build.sh.
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

RAIZ = SPECPATH + "/.."
sys.path.insert(0, RAIZ)  # para achar os pacotes config e financas

modulos = (
    collect_submodules("config")
    + collect_submodules("financas")
    + collect_submodules("django", filter=lambda nome: ".gis" not in nome and "oracle" not in nome)
    + collect_submodules("whitenoise")
    + collect_submodules("waitress")
)

dados = (
    collect_data_files("django")  # templates do admin, traduções, senhas comuns
    + [(RAIZ + "/financas/templates", "financas/templates")]
    + [(RAIZ + "/staticfiles", "staticfiles")]
)

a = Analysis(
    [RAIZ + "/desktop/ninho_desktop.py"],
    pathex=[RAIZ],
    hiddenimports=modulos,
    datas=dados,
    excludes=["psycopg", "psycopg_binary", "gunicorn", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="ninho",
    console=True,
    upx=False,
)
