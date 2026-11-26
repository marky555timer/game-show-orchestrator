#!/bin/bash
# Installs the WiFi fallback hotspot (wifi_provision.sh + wifi-provision.service, see README) on a rig.
# Same setup as the show Pi; this script just does the root steps in one go (2026-11-26, Lenovo).
#
#   1. put wifi-connect's release files next to this script (README: x86_64 build for the Lenovo,
#      aarch64 for a Pi):  wifi-connect-*-unknown-linux-gnu.tar.gz  and  wifi-connect-ui.tar.gz
#   2. sudo bash install_wifi_provision.sh
#
# Takes effect from the NEXT boot. With the usual WiFi in range it does nothing (exits within 20 s).
set -eu
cd "$(dirname "$0")"
BIN_TGZ=$(ls wifi-connect-*-unknown-linux-gnu.tar.gz | head -1)
UI_TGZ=wifi-connect-ui.tar.gz
[ -f "$BIN_TGZ" ] && [ -f "$UI_TGZ" ] || { echo "wifi-connect release files missing"; exit 1; }

tar -xzf "$BIN_TGZ" -C /usr/local/bin/
chmod 755 /usr/local/bin/wifi-connect
mkdir -p /usr/local/share/wifi-connect
tar -xzf "$UI_TGZ" -C /usr/local/share/wifi-connect/
install -m 755 wifi_provision.sh /usr/local/bin/wifi_provision.sh
install -m 644 wifi-provision.service /etc/systemd/system/wifi-provision.service
systemctl daemon-reload
systemctl enable wifi-provision.service
/usr/local/bin/wifi-connect --version || true
echo "Installed. From the next boot: no WiFi within 20 s -> open hotspot 'TriviaRig-Setup'."
