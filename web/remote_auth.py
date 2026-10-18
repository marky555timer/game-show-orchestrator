"""web/remote_auth.py
Password on the admin pages (2026-10-18). Same password as the web Show
Designer (triviacade.com/play/designer/); its hash reaches this machine
through drivers/web_sync.py (web_auth.json), set from the PC with
tools/set_admin_password.py.

  * Guests are never asked: /play, /api/player/*, the audio stream and the QR.
  * This machine itself (127.0.0.1, not via the tunnel) is never asked.
  * A login lasts config.ADMIN_SESSION_DAYS -- until "Log out", or until the
    rig's public IP changes (a new venue): the web side then moves to a new
    "epoch" and every login made under the old one ends, here and on the web.
  * Two ways in: the password form at /auth/login, or a login already made
    on triviacade.com, carried over by /play/admin.php with a one-time code
    (/auth/handoff, checked back with the web side).
  * Until a password has been set, nothing is locked.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from urllib.parse import quote

import config
from drivers import web_sync

try:
    from fastapi import Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
except ImportError:  # pragma: no cover
    Request = None

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SESSIONS_FILE = os.path.join(_BASE, "admin_sessions.json")
COOKIE = "tc_admin"

_OPEN_PREFIXES = ("/api/player/", "/auth/")
_OPEN_PATHS = {"/play", "/api/audio/stream", "/api/quiz/qr", "/favicon.ico"}

_lock = threading.Lock()
_sessions = None          # sha256(token) -> {"epoch", "created"}
_fails = {}               # client ip -> [count, first_ts]
_warned_open = False


# ---------------------------------------------------------------- sessions
def _load():
    global _sessions
    if _sessions is None:
        try:
            with open(_SESSIONS_FILE, "r", encoding="utf-8") as f:
                _sessions = json.load(f)
        except (FileNotFoundError, ValueError):
            _sessions = {}
    return _sessions


def _save():
    tmp = _SESSIONS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_sessions, f)
    os.replace(tmp, _SESSIONS_FILE)


def _new_session():
    token = secrets.token_hex(32)
    ep = web_sync.epoch()
    with _lock:
        s = _load()
        for k in [k for k, v in s.items() if v.get("epoch") != ep]:
            del s[k]
        s[hashlib.sha256(token.encode()).hexdigest()] = {"epoch": ep, "created": int(time.time())}
        _save()
    return token


def _valid(token):
    if not token:
        return False
    with _lock:
        row = _load().get(hashlib.sha256(token.encode()).hexdigest())
    return bool(row) and row.get("epoch") == web_sync.epoch()


def _end(token):
    if not token:
        return
    with _lock:
        if _load().pop(hashlib.sha256(token.encode()).hexdigest(), None) is not None:
            _save()


def _password_ok(password):
    a = web_sync.auth()
    if not a:
        return False
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(a["salt"]), int(a["iter"]), dklen=32).hex()
    return hmac.compare_digest(h, a["hash"])


# ------------------------------------------------------------------ checks
def _via_tunnel(request):
    return "cf-connecting-ip" in request.headers or "cf-ray" in request.headers


def _client_ip(request):
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")


def _is_open(path):
    return path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES)


def _trusted(request):
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1") and not _via_tunnel(request)


def _set_cookie(response, request, token):
    response.set_cookie(COOKIE, token, max_age=config.ADMIN_SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=request.headers.get("x-forwarded-proto") == "https", path="/")


def _safe_next(nxt):
    return nxt if isinstance(nxt, str) and nxt.startswith("/") and not nxt.startswith("//") else "/"


# ------------------------------------------------------------------- pages
def _login_page(nxt, error="", tunnel=False):
    web = (f'<a class="alt" href="{config.TUNNEL_REDIRECT_ADMIN_URL}?next={quote(nxt)}">'
           f'Already logged in at triviacade.com? Continue from there</a>') if tunnel else ""
    err = f'<div class="err">{error}</div>' if error else ""
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Triviacade Login</title>
<style>
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #0b0b0d; color: #f2f2f2;
         font-family: -apple-system, Segoe UI, Roboto, sans-serif; padding: 16px; box-sizing: border-box; }}
  form {{ background: #17171a; padding: 26px 22px; border-radius: 12px; width: 100%; max-width: 340px; box-sizing: border-box; }}
  h1 {{ color: #ff3b30; font-size: 1.1rem; text-transform: uppercase; letter-spacing: .08em; margin: 0 0 16px; }}
  input {{ width: 100%; box-sizing: border-box; padding: 12px; font-size: 1rem; border-radius: 8px; border: 1px solid #333;
          background: #111; color: #f2f2f2; }}
  button {{ margin-top: 12px; width: 100%; padding: 12px; font-size: 1rem; font-weight: 700; border: 0; border-radius: 8px;
           background: #ff3b30; color: #fff; cursor: pointer; }}
  .err {{ color: #ff8a80; margin-top: 10px; font-size: .9rem; }}
  .alt {{ display: block; margin-top: 16px; color: #9cc9ff; font-size: .85rem; text-align: center; }}
</style></head><body>
<form method="post" action="/auth/login">
  <h1>Show machine</h1>
  <input type="hidden" name="next" value="{nxt}">
  <input type="password" name="password" placeholder="Password" autocomplete="current-password" autofocus>
  <button>Log in</button>{err}{web}
</form></body></html>""")


# ----------------------------------------------------------------- install
def install(app):
    """web/remote_server.py, after its own middleware (this one runs first)."""

    @app.middleware("http")
    async def _admin_login_middleware(request: Request, call_next):
        global _warned_open
        path = request.url.path
        if _is_open(path) or _trusted(request):
            return await call_next(request)
        if web_sync.auth() is None:
            if not _warned_open:
                _warned_open = True
                print("[AUTH] No admin password set yet -- admin pages are open "
                      "(tools/set_admin_password.py on the PC).")
            return await call_next(request)
        if _valid(request.cookies.get(COOKIE)):
            return await call_next(request)
        if request.method == "GET" and not path.startswith("/api/"):
            return RedirectResponse(f"/auth/login?next={quote(path + ('?' + request.url.query if request.url.query else ''))}",
                                    status_code=302)
        return JSONResponse({"ok": False, "error": "login"}, status_code=401)

    @app.get("/auth/login")
    def auth_login_page(request: Request, next: str = "/"):
        return _login_page(_safe_next(next), tunnel=_via_tunnel(request))

    @app.post("/auth/login")
    async def auth_login(request: Request):
        form = await request.form()
        nxt = _safe_next(form.get("next"))
        ip = _client_ip(request)
        now = time.time()
        count, first = _fails.get(ip, [0, now])
        if now - first > 900:
            count, first = 0, now
        if count >= 10:
            return _login_page(nxt, "Too many tries -- wait 15 minutes.", _via_tunnel(request))
        if not _password_ok(str(form.get("password") or "")):
            _fails[ip] = [count + 1, first]
            time.sleep(0.4)
            return _login_page(nxt, "Wrong password.", _via_tunnel(request))
        _fails.pop(ip, None)
        response = RedirectResponse(nxt, status_code=303)
        _set_cookie(response, request, _new_session())
        print(f"[AUTH] Admin login from {ip}.")
        return response

    @app.get("/auth/handoff")
    def auth_handoff(request: Request, code: str = "", next: str = "/"):
        nxt = _safe_next(next)
        if not code or not web_sync.verify_handoff(code):
            return RedirectResponse(f"/auth/login?next={quote(nxt)}", status_code=302)
        response = RedirectResponse(nxt, status_code=302)
        _set_cookie(response, request, _new_session())
        print(f"[AUTH] Admin login carried over from triviacade.com ({_client_ip(request)}).")
        return response

    @app.post("/auth/logout")
    def auth_logout(request: Request):
        _end(request.cookies.get(COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response
