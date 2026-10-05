#!/bin/bash
# One-time macOS setup: installs Python 3.12 (if needed), project dependencies, and .env
set -e
cd "$(dirname "$0")"

PY=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12

if [ ! -x "$PY" ]; then
  echo "== Installing Python 3.12 (you will be asked for your Mac password) =="
  curl -L -o /tmp/python312.pkg https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg
  sudo installer -pkg /tmp/python312.pkg -target /
fi

echo "== Installing SSL certificates for Python =="
bash "/Applications/Python 3.12/Install Certificates.command" >/dev/null 2>&1 || true

echo "== Setting up project environment =="
"$PY" -m venv .venv
.venv/bin/pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  echo "== Anypoint credentials =="
  read -r -p "Client ID: " CID
  read -r -s -p "Client Secret (hidden while typing): " CSECRET
  echo
  KEY=$(.venv/bin/python -c "import secrets; print(secrets.token_hex(24))")
  cat > .env <<EOF
ANYPOINT_CLIENT_ID=$CID
ANYPOINT_CLIENT_SECRET=$CSECRET
FLASK_SECRET_KEY=$KEY
EOF
  chmod 600 .env
fi

echo
echo "Setup complete. Start the app with:  bash run_mac.sh"
