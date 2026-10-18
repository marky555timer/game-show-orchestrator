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
_last_report_result = None   # last redirector POST outcome ("ok"/"HTTP 404"/...), for change-only logging
_accepted_url = None         # the tunnel URL the redirector last said "ok" to
_last_post_at = 0.0


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
    global _last_report_result, _accepted_url, _last_post_at
    _last_post_at = time.monotonic()
    try:
        r = requests.post(
            config.TUNNEL_REDIRECT_UPDATE_URL,
            json={"secret": config.TUNNEL_REDIRECT_SECRET, "url": url},
            timeout=5.0,
        )
        result = "ok" if r.status_code == 200 else f"HTTP {r.status_code}"
        if result == "ok":
            # the reply carries the admin-login epoch (a new public IP = logged out)
            from drivers import web_sync
            web_sync.note_epoch((r.json() or {}).get("epoch"))
    except Exception as e:
        result = f"error: {e}"
    if result == "ok":
        _accepted_url = url
    # Heartbeats run every minute: log only when the outcome changes, so a
    # redirector that starts answering 404/403 (e.g. wiped by a site
    # redeploy, 2026-10-10) shows up once instead of hiding or spamming.
    if result != _last_report_result:
        if result == "ok":
            print(f"[TUNNEL] Redirector accepted tunnel URL ({config.TUNNEL_REDIRECT_UPDATE_URL}).")
        else:
            print(f"[TUNNEL] Failed to report tunnel URL to the redirector ({config.TUNNEL_REDIRECT_UPDATE_URL}): {result}")
        _last_report_result = result


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
    registered = False
    for line in proc.stderr:
        line = line.rstrip("\n")
        if line:
            print(f"[CLOUDFLARED] {line}")
        if "Registered tunnel connection" in line:
            registered = True
        elif "Lost connection with the edge" in line or "Connection terminated" in line:
            registered = False
        if _DNS_REFRESH_FAILURE_RE.search(line) and registered:
            # 2026-10-03: with --protocol http2 a DNS-refresh timeout while
            # an edge connection is registered is harmless, but killing here
            # threw away a working tunnel AND its URL (a new quick-tunnel URL
            # each respawn), stranding the operator's open page on a dead
            # address (error 1033) -- twice in 60s on a lossy WiFi link.
            # Only respawn when there is no live edge connection to keep.
            print("[TUNNEL] DNS resolver refresh failed, but the edge connection is up -- keeping this tunnel.")
        elif _DNS_REFRESH_FAILURE_RE.search(line):
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
    global _proc
    while True:
        proc = _spawn_process(cloudflared_path)
        _proc = proc
        if proc is not None:
            _set_status("connecting")
            print("[TUNNEL] cloudflared starting -- public URL will appear shortly.")
            _reader_loop(proc)
        if state.shutdown_requested:
            return
        print(f"[TUNNEL] Retrying in {config.TUNNEL_RETRY_SECONDS:.0f}s...")
        time.sleep(config.TUNNEL_RETRY_SECONDS)


_proc = None  # the live cloudflared subprocess (for the network watchdog below)
_NET_POLL_SECONDS = 4.0
_NET_OUTAGE_RESTART_SECONDS = 15.0


def _local_ip():
    """The address the Pi would use to reach the internet, or None with no
    route. A UDP connect sends no packets -- it only picks the interface."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(1.0)
        s.connect(("1.1.1.1", 53))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _network_watchdog():
    """2026-10-02: a WiFi re-association handed the Pi a new DHCP address
    (.19 -> .25). cloudflared's edge connection was bound to the old one; it
    logged nothing, kept reporting 1 healthy connection, and Cloudflare
    dropped the tunnel (error 1033) until restart. So: when the LAN address
    changes, or the network is gone for _NET_OUTAGE_RESTART_SECONDS+, kill
    cloudflared -- _run_loop respawns it and reports the new public URL."""
    last_ip = None
    down_since = None
    while True:
        time.sleep(_NET_POLL_SECONDS)
        if state.shutdown_requested:
            return
        ip = _local_ip()
        reason = None
        if ip is None:
            down_since = down_since or time.time()
            continue
        if down_since is not None and time.time() - down_since >= _NET_OUTAGE_RESTART_SECONDS:
            reason = f"network was down {time.time() - down_since:.0f}s"
        elif last_ip is not None and ip != last_ip:
            reason = f"LAN address changed {last_ip} -> {ip}"
        down_since = None
        last_ip = ip
        proc = _proc
        if reason and proc is not None and proc.poll() is None:
            print(f"[TUNNEL] {reason} -- restarting cloudflared so the tunnel re-registers.")
            time.sleep(2.0)  # let DHCP/routes settle before it redials
            proc.kill()


def _heartbeat_loop():
    """Independent of _reader_loop (which only wakes on new subprocess
    output) -- re-POSTs the current URL on a plain wall-clock timer so
    cloudflare_redirect/'s staleness check never times out during a long
    quiet stretch with no new cloudflared log lines. Until the redirector has
    accepted the current URL (a cold boot's first POST often fails while
    WiFi/DNS are still coming up, 2026-10-17), it retries every
    config.TUNNEL_REPORT_RETRY_SECONDS instead -- otherwise the QR sends
    guests to the previous boot's dead tunnel for up to a minute."""
    while True:
        time.sleep(config.TUNNEL_REPORT_RETRY_SECONDS)
        url = get_current_tunnel_url()
        if not url:
            continue
        if url != _accepted_url or time.monotonic() - _last_post_at >= config.TUNNEL_HEARTBEAT_SECONDS:
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
    threading.Thread(target=_network_watchdog, daemon=True).start()
