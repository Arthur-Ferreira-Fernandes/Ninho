#!/bin/sh
# Gera o executável dist/ninho e o atalho no menu de aplicativos.
#   desktop/build.sh
set -e
cd "$(dirname "$0")/.."

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt -r desktop/requirements.txt

rm -rf staticfiles
DJANGO_DEBUG=0 DJANGO_SECRET_KEY=build .venv/bin/python manage.py collectstatic --noinput -v 0

.venv/bin/pyinstaller --noconfirm --clean --log-level WARN \
    --distpath dist --workpath build desktop/ninho.spec

# atalho no menu de aplicativos, apontando para este executável
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/ninho.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Ninho
Comment=Finanças da família
Exec=$(pwd)/dist/ninho
Icon=$(pwd)/financas/static/financas/img/icone-512.png
Terminal=true
Categories=Office;Finance;
EOF

echo "Pronto: $(pwd)/dist/ninho (e o atalho Ninho no menu de aplicativos)"
