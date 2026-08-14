"""drivers/tunnel_engine.py
Static QR / non-WiFi guest access (2026-08-14): runs `cloudflared tunnel
--url http://localhost:<port>` as a background subprocess, giving this
app's local web remote a public HTTPS address reachable from ANY network
(cellular included), not just guests on the same WiFi/LAN the QR-encoded
LAN IP requires today.

Cloudflare's free "quick tunnel" mode needs no account or domain -- it just
hands back a random https://xxxx.trycloudflare.com URL each time it
starts, which is why the QR itself can't encode it directly (it changes on
every app restart). Instead, this module reports that URL to a tiny PHP
redirector deployed on the operator's OWN static web server (see
cloudflare_redirect/ at the repo root) via a pre-shared-secret POST -- the
QR encodes THAT static, never-changing URL (web/net_info.py::
get_admin_url()/get_play_url()), and the PHP script 302s guests to
wherever the tunnel currently is. See cloudflare_redirect/README.md for
the deploy-side half of this.

Entirely optional and self-disabling: if `cloudflared` isn't installed, or
config.TUNNEL_REDIRECT_UPDATE_URL isn't set, start() logs once and returns
-- the app falls back to the plain LAN-IP QR, unchanged from before this
feature existed."""
import re
import shutil
import subprocess
import threading
import time

import config

try:
    import requests
except ImportError:
    requests = None

_TUNNEL_URL_RE = re.compile(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com")

# "disabled" (not configured) | "not_installed" (cloudflared missing) |
# "connecting" (subprocess started, no URL yet) | "live" (URL confirmed) |
# "stopped" (was live, process ended). 2026-08-14: added after a report
# that console prints alone weren't visible -- a packaged/frozen build can
# run with no console window at all, so nothing printed here is guaranteed
# to ever reach the operator. graphics/overlay_panel.py reads this to show
# tunnel status directly on the rig's own always-visible display instead.
_status = "disabled"
_current_tunnel_url = ""
_lock = threading.Lock()


def get_status():
    with _lock:
        return _status


def get_current_tunnel_url():
    """Read-only status check -- "" means no tunnel is currently up."""
    with _lock:
        return _current_tunnel_url


def _set_status(value):
    global _status
    with _lock:
        _status = value


def _post_update(url):
    if requests is None or not config.TUNNEL_REDIRECT_UPDATE_URL:
        return
    try:
        requests.post(
            config.TUNNEL_REDIRECT_UPDATE_URL,
            json={"secret": config.TUNNEL_REDIRECT_SECRET, "url": url},
            timeout=5.0,
        )
    except Exception as e:
        print(f"[TUNNEL] Failed to report tunnel URL to the redirector: {e}")


def _reader_loop(proc):
    """Reads cloudflared's stderr line by line (it logs the assigned quick-
    tunnel URL there, not stdout) for as long as the process lives,
    updating _current_tunnel_url and reporting it to the redirector the
    instant a (new) URL is found. When the process ends, clears the URL
    rather than leaving a dead one claimed -- see the heartbeat loop below,
    which stops re-posting once this is empty.

    Every line is also echoed to the console (prefixed [CLOUDFLARED]) --
    2026-08-14 fix: this used to only scan stderr for the URL pattern and
    silently discard everything else, so a connection failure (blocked
    outbound port, DNS issue, etc.) produced no visible error at all, just
    an admin/guest QR that never stopped falling back to LAN-only. Now
    whatever cloudflared itself reports shows up right in the app's
    console for troubleshooting."""
    global _current_tunnel_url
    for line in proc.stderr:
        line = line.rstrip("\n")
        if line:
            print(f"[CLOUDFLARED] {line}")
        match = _TUNNEL_URL_RE.search(line)
        if not match:
            continue
        found = match.group(0)
        with _lock:
            changed = found != _current_tunnel_url
            _current_tunnel_url = found
        if changed:
            print(f"[TUNNEL] Public URL: {found}")
            _set_status("live")
            _post_update(found)

    print("[TUNNEL] cloudflared process ended -- public URL no longer available "
          "(restart the app to reconnect).")
    with _lock:
        _current_tunnel_url = ""
    _set_status("stopped")


def _heartbeat_loop():
    """Independent of _reader_loop (which only wakes on new subprocess
    output) -- re-POSTs the current URL on a plain wall-clock timer so
    cloudflare_redirect/'s staleness check never times out during a long
    quiet stretch with no new cloudflared log lines."""
    while True:
        time.sleep(config.TUNNEL_HEARTBEAT_SECONDS)
        url = get_current_tunnel_url()
        if url:
            _post_update(url)


def start():
    """Call once at app startup (main.py). Safe no-op if the redirector
    isn't configured or cloudflared isn't installed -- see module
    docstring."""
    if not config.TUNNEL_REDIRECT_UPDATE_URL:
        print("[TUNNEL] config.TUNNEL_REDIRECT_UPDATE_URL not set -- static/non-WiFi QR disabled, "
              "using the LAN-IP QR only.")
        _set_status("disabled")
        return

    cloudflared_path = shutil.which("cloudflared")
    if cloudflared_path is None:
        print("[TUNNEL] cloudflared not found on PATH -- install it "
              "(https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) "
              "to enable non-WiFi guest access. Falling back to the LAN-IP QR only.")
        _set_status("not_installed")
        return

    try:
        proc = subprocess.Popen(
            [cloudflared_path, "tunnel", "--url", f"http://localhost:{config.WEB_REMOTE_PORT}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except Exception as e:
        print(f"[TUNNEL] Failed to start cloudflared: {e}")
        _set_status("not_installed")
        return

    _set_status("connecting")
    threading.Thread(target=_reader_loop, args=(proc,), daemon=True).start()
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    print("[TUNNEL] cloudflared starting -- public URL will appear shortly.")
