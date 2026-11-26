#!/bin/bash
# WiFi fallback hotspot (2026-08-16). Runs at boot via wifi-provision.service,
# entirely independent of the game-show-orchestrator app itself -- a bug here
# can never take down the show, and the app's own autostart (start.sh, via
# the desktop session) is not gated on this at all; it starts on its usual
# schedule regardless of network state.
#
# If NetworkManager already has a working WiFi connection (the normal case
# every night after the first setup at a venue), this exits within a minute and
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
    # a real WiFi connection -- not our own hotspot (NetworkManager calls that "connected" too)
    nmcli -t -f TYPE,STATE,CONNECTION dev status 2>/dev/null | grep '^wifi:connected:' \
        | grep -qv ':TriviaRig-Setup$'
}

wait_for_wifi() {   # $1 = seconds. Fresh scans along the way (see below). 0 = connected.
    local i
    for i in $(seq 1 "$1"); do
        is_wifi_connected && return 0
        [ $((i % 15)) -eq 1 ] && nmcli dev wifi rescan >/dev/null 2>&1
        sleep 1
    done
    return 1
}

# 2026-11-26 (Lenovo): 20 s wasn't enough. With no country set, its Intel card finds 5 GHz networks by
# listening only (passive scan) -- slow -- so a 5 GHz-only router ("baby") wasn't found in time: no
# auto-connect, and missing from the hotspot's list too. Wait longer, with fresh scans.
echo "$LOG_TAG Checking for an existing WiFi connection..."
if wait_for_wifi 60; then
    echo "$LOG_TAG Already connected -- nothing to do."
    exit 0
fi

echo "$LOG_TAG No WiFi connection after 60s -- opening the setup hotspot."
while ! is_wifi_connected; do
    nmcli dev wifi rescan >/dev/null 2>&1
    sleep 10            # let the scan (5 GHz included) finish: wifi-connect lists what it finds at start
    # --activity-timeout: with nobody using the setup page for 2 minutes wifi-connect exits; a known
    # network that came up meanwhile (the router switched on late) then gets 30 s to auto-connect, and
    # if it doesn't the hotspot reopens with a fresh list.
    "$WIFI_CONNECT_BIN" \
        --portal-ssid "TriviaRig-Setup" \
        --ui-directory "$WIFI_CONNECT_UI" \
        --activity-timeout 120
    wait_for_wifi 30 && break
done

echo "$LOG_TAG Connected -- rebooting for a clean handoff."
sleep 3
/sbin/reboot
