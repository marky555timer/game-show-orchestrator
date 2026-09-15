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
from state import state

try:
    import requests
except ImportError:
    requests = None

# Requires at least one hyphen (2+ dictionary-word segments), matching how
# cloudflared actually generates quick-tunnel slugs (e.g.
# "vol-selling-application-retailer.trycloudflare.com"). A looser pattern
# here used to also match "https://api.trycloudflare.com" -- Cloudflare's
# own fixed API hostname, which shows up inside cloudflared's *failure*
# message ("failed to request quick Tunnel: Post
# 'https://api.trycloudflare.com/tunnel': ...") when the tunnel request
# itself couldn't get out (e.g. no network yet). That false match made
# _reader_loop treat a failed connection as a live tunnel and report the
# dead api.trycloudflare.com URL to the redirector as if it worked.
_TUNNEL_URL_RE = re.compile(r"https://[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)+\.trycloudflare\.com")

# Matches cloudflared's own log lines for the DNS-refresh failure that
# precedes a burst of dropped/"context canceled" requests -- see
# _reader_loop's watchdog below.
_DNS_REFRESH_FAILURE_RE = re.compile(r"Failed to (?:refresh|initialize) DNS local resolver")

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


# Public-internet reachability probe (2026-08-16), shared by the Setup
# page's status row (web/remote_server.py) and the LED matrix's own Setup-
# phase status chips (graphics/matrix_canvas.py) -- cached/rate-limited so
# neither caller's poll loop ever blocks on a down network.
_net_check_cache = {"reachable": False, "checked_at": 0.0}
_NET_CHECK_INTERVAL_S = 15.0


def internet_reachable():
    now = time.time()
    if now - _net_check_cache["checked_at"] < _NET_CHECK_INTERVAL_S:
        return _net_check_cache["reachable"]
    reachable = False
    if requests is not None:
        try:
            requests.head("https://1.1.1.1", timeout=1.5)
            reachable = True
        except Exception:
            reachable = False
    _net_check_cache["reachable"] = reachable
    _net_check_cache["checked_at"] = now
    return reachable


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
        if _DNS_REFRESH_FAILURE_RE.search(line):
            # Self-healing watchdog (2026-09-08): this line means
            # cloudflared's edge routing has gone stale -- it keeps running
            # and keeps LOOKING alive (no exit, no status change), but
            # starts silently dropping in-flight requests ("context
            # canceled") until something kills it. --protocol http2 above
            # should make this rare, but if it still happens, force a clean
            # respawn now rather than leaving the tunnel degraded for the
            # rest of the show. _run_loop's existing retry (short sleep,
            # then _spawn_process again) picks this up the instant
            # proc.stderr closes below.
            print("[TUNNEL] DNS resolver refresh failed -- restarting cloudflared to recover.")
            proc.kill()
            break
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

    print("[TUNNEL] cloudflared process ended.")
    with _lock:
        _current_tunnel_url = ""
    _set_status("stopped")


def _spawn_process(cloudflared_path):
    try:
        return subprocess.Popen(
            # --protocol http2 (2026-09-08): cloudflared defaults to QUIC,
            # which leans on a periodic UDP DNS lookup of
            # region1.v2.argotunnel.com to refresh edge routing. Confirmed
            # live in startup.log (recurring since at least 2026-08-24,
            # independent of anything else running): when that lookup times
            # out ("Failed to refresh DNS local resolver"), cloudflared
            # drops in-flight requests ("Incoming request ended abruptly:
            # context canceled") without the process actually exiting -- so
            # _run_loop's crash-retry below never notices. Root cause of
            # both the operator web-remote going unresponsive mid-import
            # (2026-09-08) and a multiplayer session's /api/player/lock
            # calls silently dying (2026-08-29), NOT app-side CPU/thread
            # contention. HTTP/2 uses a plain persistent TCP connection
            # instead, sidestepping this UDP-DNS refresh path entirely.
            [cloudflared_path, "tunnel", "--protocol", "http2", "--url", f"http://localhost:{config.WEB_REMOTE_PORT}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except Exception as e:
        print(f"[TUNNEL] Failed to start cloudflared: {e}")
        return None


def _run_loop(cloudflared_path):
    """Owns cloudflared's full lifecycle for the life of the app: spawn,
    stream its output until it exits, then retry after a short delay --
    unless the app itself is shutting down.

    2026-08-29: added after a cold-boot report that the public-tunnel QR
    sometimes never came up. Root cause: this used to be a single one-shot
    spawn with no retry -- fine on a warm restart (network already up), but
    on a cold boot start.sh's own `sleep 5` is nowhere near
    wifi_provision.sh's own up-to-20s connect window, so cloudflared could
    spawn before the interface had a route at all (confirmed in the logs:
    "dial udp 192.168.1.1:53: connect: network is unreachable"), exit
    immediately, and just stay down for the rest of that session -- no
    visible symptom until a guest's QR failed to load."""
    while True:
        proc = _spawn_process(cloudflared_path)
        if proc is not None:
            _set_status("connecting")
            print("[TUNNEL] cloudflared starting -- public URL will appear shortly.")
            _reader_loop(proc)
        if state.shutdown_requested:
            return
        print(f"[TUNNEL] Retrying in {config.TUNNEL_RETRY_SECONDS:.0f}s...")
        time.sleep(config.TUNNEL_RETRY_SECONDS)


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

    threading.Thread(target=_run_loop, args=(cloudflared_path,), daemon=True).start()
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
