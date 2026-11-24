#!/bin/bash
# Disc Tools AppImage launcher.
# The GUI runtime (Python + Qt) is bundled and read-only. The
# engines are not bundled: on first run the app sets them up in a
# per-user folder -- distro packages (xorriso, growisofs, cdrdao,
# GNU ddrescue) are detected and offered as a one-click install
# when missing, and redumper is downloaded and hash-verified.
# Later runs find everything in under a second and skip setup.
HERE="$(dirname "$(readlink -f "$0")")"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/disc-tools"
mkdir -p "$DATA/engine"
export DISC_TOOLS_ENGINE_DIR="$DATA/engine"
export DISC_TOOLS_DATA_DIR="$DATA"
PY="$HERE/opt/python3.12/bin/python3.12"
CERT="$HERE/opt/python3.12/lib/python3.12/site-packages/certifi/cacert.pem"
if [ -f "$CERT" ]; then
  export SSL_CERT_FILE="$CERT"
fi
exec "$PY" "$HERE/usr/share/disc-tools/app/main.py" "$@"
