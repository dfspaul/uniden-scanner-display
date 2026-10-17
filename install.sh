#!/bin/sh
# Install display.py as a systemd service that starts at boot, running as you from this folder.
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
sed -e "s|@USER@|$(id -un)|" -e "s|@DIR@|$DIR|g" "$DIR/scanner-display.service" | sudo tee /etc/systemd/system/scanner-display.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now scanner-display
echo "Running: http://$(hostname).local/"
