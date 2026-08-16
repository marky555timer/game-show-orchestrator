#!/bin/bash
# WiFi fallback hotspot (2026-08-16). Runs at boot via wifi-provision.service,
# entirely independent of the game-show-orchestrator app itself -- a bug here
# can never take down the show, and the app's own autostart (start.sh, via
# the desktop session) is not gated on this at all; it starts on its usual
# schedule regardless of network state.
#
# If NetworkManager already has a working WiFi connection (the normal case
# every night after the first setup at a venue), this exits in under 20s and
# never touches anything else, ever. Only when NO WiFi is connected does it
# open an unsecured "TriviaRig-Setup" access point + captive portal
# (balena wifi-connect) so the operator can hand it the venue's real WiFi
# credentials from their own phone -- open/unsecured is deliberate, it's
# what makes a phone's own OS auto-pop the captive-portal login prompt
# reliably (same UX as hotel/airport WiFi).
#
# wifi-connect's own documented behavior: on credential submission it
# disables the AP and attempts the new connection; on failure it re-enables
# the AP for another attempt automatically -- no separate retry logic is
# needed here. Because the AP is torn down as part of every connection
# attempt (success or failure), there is no way to serve a confirmation page
# back to the phone afterward -- the portal's own network is gone the
# instant the AP disables. So instead of a "tap to reboot" step inside the
# portal (not technically possible with this tool), this script reboots
# automatically the moment nmcli confirms the new connection is up.
set -u
LOG_TAG="[WIFI PROVISION]"
WIFI_CONNECT_BIN="/usr/local/bin/wifi-connect"
WIFI_CONNECT_UI="/usr/local/share/wifi-connect"

is_wifi_connected() {
    nmcli -t -f TYPE,STATE dev status 2>/dev/null | grep -q '^wifi:connected$'
}

echo "$LOG_TAG Checking for an existing WiFi connection..."
for i in $(seq 1 20); do
    if is_wifi_connected; then
        echo "$LOG_TAG Already connected -- nothing to do."
        exit 0
    fi
    sleep 1
done

echo "$LOG_TAG No WiFi connection after 20s -- opening the setup hotspot."
while ! is_wifi_connected; do
    "$WIFI_CONNECT_BIN" \
        --portal-ssid "TriviaRig-Setup" \
        --ui-directory "$WIFI_CONNECT_UI"
    sleep 2
done

echo "$LOG_TAG Connected -- rebooting for a clean handoff."
sleep 3
/sbin/reboot
