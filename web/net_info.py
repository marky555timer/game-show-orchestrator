# web/net_info.py
"""LAN IP resolution, shared by web/remote_server.py (what uvicorn binds
display-wise) and web/qr_popup.py (what URL the QR code encodes). Kept as
its own leaf module -- rather than having qr_popup import remote_server --
so importing qr_popup from inputs/gamepad.py can never form an import cycle
back through remote_server (which itself imports inputs.gamepad to reuse
the quiz select/grade/clear action functions)."""
import socket

import config
from drivers import tunnel_engine


def get_lan_ip():
    """Best-effort LAN-facing IP via the classic UDP-connect trick (no
    packets actually sent -- connect() on a UDP socket just picks the local
    interface/route the OS would use). Falls back to the hostname lookup,
    then to loopback, if that fails (e.g. no network at all)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        pass

    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def get_admin_url():
    """Operator/admin remote URL for every QR call site (web/qr_popup.py,
    graphics/overlay_panel.py, web/remote_server.py). Uses the static,
    never-changing redirector URL (config.TUNNEL_REDIRECT_ADMIN_URL, see
    drivers/tunnel_engine.py) ONLY when a Cloudflare Tunnel is actually
    confirmed live right now -- otherwise falls back to the plain LAN-IP
    URL, same as before this feature existed. 2026-08-14 fix: the static
    URL used to be returned unconditionally just because it was
    *configured*, whether or not cloudflared had actually connected --
    which meant admin access before a show even starts (Setup/Countdown)
    was silently gated on an internet tunnel being up, when it should
    always work over the venue's own WiFi/LAN regardless. Guest-facing
    non-WiFi access still automatically upgrades to the tunnel URL the
    instant it comes online (drivers/tunnel_engine.py's reader thread)."""
    if config.TUNNEL_REDIRECT_ADMIN_URL and tunnel_engine.get_current_tunnel_url():
        return config.TUNNEL_REDIRECT_ADMIN_URL
    return f"http://{get_lan_ip()}:{config.WEB_REMOTE_PORT}"


def get_play_url():
    """Guest-facing /play URL -- same tunnel-confirmed-live-first, LAN-IP-
    fallback logic as get_admin_url() above."""
    if config.TUNNEL_REDIRECT_PLAY_URL and tunnel_engine.get_current_tunnel_url():
        return config.TUNNEL_REDIRECT_PLAY_URL
    return f"http://{get_lan_ip()}:{config.WEB_REMOTE_PORT}/play"
