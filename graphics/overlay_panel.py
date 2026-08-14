# graphics/overlay_panel.py
"""Right-side diagnostic/control panel (Feature Update): drawn into the
extra window space config.OVERLAY_PANEL_WIDTH_PX adds to the right of the
LED-matrix simulator (see config.py's WINDOW_W/OVERLAY_PANEL_* constants).

This is a diagnostic/control surface, not part of the LED-matrix
simulation, so it deliberately uses a normal pygame.font (readable at
normal desktop viewing distance) rather than graphics/text_render.py's 5x7
bitmap font, which exists purely to simulate what the physical LED hardware
can display.

Owns both rendering (render()) and click handling (handle_click()) for
everything in the panel -- inputs/gamepad.py's MOUSEBUTTONDOWN handler just
forwards the raw click position to hit_test()/handle_click(), the same
"module owns its own popup/panel behavior" split already used by
web/qr_popup.py for the QR popup."""
import time

import pygame

try:
    import psutil
except ImportError:
    psutil = None

try:
    import qrcode
except ImportError:
    qrcode = None

import config
from state import state
from drivers import token_tracker
from drivers import deck_orchestrator
from drivers import auto_dj_engine
from drivers.midi_driver import handle_dj_volume
from drivers.dmx_driver import dmx
from drivers import led_bridge
from drivers import tunnel_engine
from web.net_info import get_admin_url, get_play_url

_TUNNEL_STATUS_LABELS = {
    "disabled": "off",
    "not_installed": "no cloudflared",
    "connecting": "connecting",
    "live": "LIVE",
    "stopped": "stopped",
}

pygame.font.init()
_font = pygame.font.SysFont("consolas", 13)
_font_bold = pygame.font.SysFont("consolas", 13, bold=True)
_font_tiny = pygame.font.SysFont("consolas", 11)

_BG = (18, 18, 20)
_TEXT = (230, 230, 230)
_DIM = (150, 150, 150)
_ACCENT = (255, 59, 48)
_BTN_BG = (40, 40, 44)
_BTN_BORDER = (70, 70, 76)
_DANGER_BG = (74, 15, 15)

_session_start = time.time()

if psutil is not None:
    psutil.cpu_percent(interval=None)  # prime the internal sampler

# Rebuilt every render() call -- [{"id": str, "rect": pygame.Rect}]
_click_regions = []

_PANEL_X = config.MATRIX_RENDER_WIDTH_PX
_PAD = 10
_ROW_H = 16
_COL_GAP = 14

# Column layout: config.WINDOW_H is now the real 800x480 touchscreen height
# (see config.py), not the old OCR-constrained MATRIX_RENDER_HEIGHT_PX, so
# there's real vertical room per column now -- still organized as columns
# rather than one long scrolling list, since that's simplest for a
# fixed-size touchscreen with no scroll gesture wired up.
_COL1_W = 190   # QR block -- widened 2026-08-09 for an easier-to-scan/tap QR
_COL2_W = 190   # diagnostics + auto-dj
_COL3_W = config.OVERLAY_PANEL_WIDTH_PX - _PAD * 2 - _COL_GAP * 2 - _COL1_W - _COL2_W

_COL1_X = _PANEL_X + _PAD
_COL2_X = _COL1_X + _COL1_W + _COL_GAP
_COL3_X = _COL2_X + _COL2_W + _COL_GAP

# --- QR block (column 1) -- admin remote / guest play, tap to toggle ---
# Persistent (not a popup, unlike web/qr_popup.py's Btn3-triggered version)
# so the host/guests can scan it straight off the always-on-top canvas
# simulator window. Tapping the QR flips between the two URLs it can show
# (2026-08-09): "admin" is the existing operator web remote, "client" is
# /play, the multiplayer QR quiz sign-up page. Re-resolved every
# _QR_REFRESH_INTERVAL_SECONDS (not every frame -- get_lan_ip() opens a
# socket) in case the LAN IP changes (Wi-Fi reconnect mid-show).
_QR_MAX_PX = 180
_QR_REFRESH_INTERVAL_SECONDS = 15.0
_qr_mode = "admin"  # "admin" | "client"
_qr_cache = {}  # mode -> (surface, url, checked_at)


def _build_qr_surface(url):
    if qrcode is None:
        return None
    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    surf = pygame.image.fromstring(img.tobytes(), img.size, "RGB")
    if surf.get_width() > _QR_MAX_PX:
        surf = pygame.transform.smoothscale(surf, (_QR_MAX_PX, _QR_MAX_PX))
    return surf


def _url_for_mode(mode):
    return get_play_url() if mode == "client" else get_admin_url()


def _get_qr_surface_and_url():
    global _qr_cache
    now = time.time()
    cached = _qr_cache.get(_qr_mode)
    if cached is not None and now - cached[2] < _QR_REFRESH_INTERVAL_SECONDS:
        return cached[0], cached[1]

    url = _url_for_mode(_qr_mode)
    if cached is None or url != cached[1]:
        surf = _build_qr_surface(url)
    else:
        surf = cached[0]
    _qr_cache[_qr_mode] = (surf, url, now)
    return surf, url


def hit_test(pos):
    """Returns the clicked region's id, or None. Called from
    inputs/gamepad.py's MOUSEBUTTONDOWN handler."""
    for region in _click_regions:
        if region["rect"].collidepoint(pos):
            return region["id"]
    return None


def handle_click(click_id):
    """Performs the action for a clicked region id. Called right after
    hit_test() returns a non-None id."""
    if click_id == "toggle_qr_mode":
        global _qr_mode
        _qr_mode = "client" if _qr_mode == "admin" else "admin"
        print(f"[OVERLAY PANEL] QR toggled -> showing {_qr_mode} QR.")
    elif click_id == "suppress_ai_checkbox":
        state.ai_suppressed = not state.ai_suppressed
        print(f"[OVERLAY PANEL] Suppress AI Functions -> {state.ai_suppressed}")
    elif click_id == "autodj_minus10":
        state.auto_dj_track_started_at -= config.WEB_AUTODJ_SKIP_SECONDS
    elif click_id == "autodj_plus10":
        state.auto_dj_track_started_at = min(
            time.time(), state.auto_dj_track_started_at + config.WEB_AUTODJ_ADD_SECONDS
        )
    elif click_id == "test_westminster":
        # Lazy import: avoids a hard import-order dependency between this
        # module and drivers/westminster_engine.py at process startup.
        from drivers import westminster_engine
        westminster_engine.trigger_test()
    elif click_id == "show_start_game":
        # Same immediate-start action as the web remote's Setup page "Start
        # Game" button -- valid from "setup"/"countdown"/"outro" (skips the
        # rest of a countdown, or starts the next show right after a
        # previous one's outro); a safe no-op otherwise, guarded inside
        # start_intro() itself. Lazy import, same rationale as westminster
        # above.
        from drivers import show_engine
        show_engine.start_intro()
    elif click_id == "show_stop_game":
        from drivers import show_engine
        show_engine.stop_show()
    elif click_id == "vol_down":
        handle_dj_volume(-5)
    elif click_id == "vol_up":
        handle_dj_volume(5)
    elif click_id == "prev_track":
        deck_orchestrator.trigger_track_move("back")
        auto_dj_engine.notify_manual_track_move()
    elif click_id == "next_track":
        deck_orchestrator.trigger_track_move("next")
        auto_dj_engine.notify_manual_track_move()
    elif click_id == "close_app":
        print("[OVERLAY PANEL] CLOSE APP requested via desktop overlay.")
        state.shutdown_reason = "UI OVERLAY BUTTON"
        state.shutdown_requested = True


def _format_cost(cost):
    """"$.12/hr" style below $1, "$1.42/hr" style at/above $1 -- per the
    feature spec's compact cost readout."""
    text = f"${cost:.2f}"
    if cost < 1.0:
        text = text.replace("$0.", "$.")
    return f"{text}/hr"


def _draw_text(screen, text, x, y, color=_TEXT, bold=False):
    font = _font_bold if bold else _font
    screen.blit(font.render(text, True, color), (x, y))


def _draw_button(screen, click_id, rect, label, danger=False):
    bg = _DANGER_BG if danger else _BTN_BG
    pygame.draw.rect(screen, bg, rect, border_radius=4)
    pygame.draw.rect(screen, _BTN_BORDER, rect, 1, border_radius=4)
    label_surf = _font.render(label, True, _TEXT)
    lx = rect.x + (rect.width - label_surf.get_width()) // 2
    ly = rect.y + (rect.height - label_surf.get_height()) // 2
    screen.blit(label_surf, (lx, ly))
    _click_regions.append({"id": click_id, "rect": rect})


def _draw_checkbox(screen, click_id, x, y, label, checked):
    box = pygame.Rect(x, y, 16, 16)
    pygame.draw.rect(screen, _BTN_BG, box, border_radius=3)
    pygame.draw.rect(screen, _BTN_BORDER, box, 1, border_radius=3)
    if checked:
        pygame.draw.rect(screen, _ACCENT, box.inflate(-6, -6), border_radius=2)
    _draw_text(screen, label, x + 24, y - 1)
    hit_rect = pygame.Rect(x, y, box.width + 24 + _font.size(label)[0], box.height)
    _click_regions.append({"id": click_id, "rect": hit_rect})


def _cost_per_hour():
    input_tokens, output_tokens, total = token_tracker.get_totals()
    cost = (input_tokens * config.HAIKU_INPUT_COST_PER_MTOK
            + output_tokens * config.HAIKU_OUTPUT_COST_PER_MTOK) / 1_000_000.0
    elapsed_hours = max(1e-6, (time.time() - _session_start) / 3600.0)
    return total, cost / elapsed_hours


def render(screen, t):
    """Called once per frame from graphics/matrix_canvas.py::render_led_grid(),
    right before pygame.display.flip(). Laid out in fixed-height columns
    (see the _COL*_W/_COL*_X constants above) that must never exceed
    config.WINDOW_H -- add new content as a new column, not more rows."""
    global _click_regions
    _click_regions = []

    panel_rect = pygame.Rect(_PANEL_X, 0, config.OVERLAY_PANEL_WIDTH_PX, config.WINDOW_H)
    pygame.draw.rect(screen, _BG, panel_rect)

    # --- Column 1: QR (tap to toggle admin remote <-> guest play link) ---
    x, y = _COL1_X, _PAD
    label = "SCAN FOR REMOTE (ADMIN)" if _qr_mode == "admin" else "SCAN TO PLAY (GUEST)"
    _draw_text(screen, label, x, y, color=_ACCENT, bold=True)
    y += _ROW_H
    qr_surf, remote_url = _get_qr_surface_and_url()
    qr_top = y
    if qr_surf is not None:
        screen.blit(qr_surf, (x, y))
        y += qr_surf.get_height() + 4
    else:
        _draw_text(screen, "qrcode/Pillow", x, y, color=_DIM)
        y += _ROW_H
        _draw_text(screen, "not installed", x, y, color=_DIM)
        y += _ROW_H
    screen.blit(_font_tiny.render(remote_url, True, _TEXT), (x, y))
    y += _ROW_H
    # Whole QR block is tappable -- covers the label, image, and URL text,
    # not just the image itself, so it's an easy target on a touchscreen.
    _click_regions.append({"id": "toggle_qr_mode", "rect": pygame.Rect(x, _PAD, _COL1_W - _PAD, y - _PAD)})
    _draw_text(screen, "(tap to switch)", x, y, color=_DIM)

    # --- Column 2: diagnostics + auto-dj ---
    x = _COL2_X
    y = _PAD
    w = _COL2_W
    _draw_text(screen, "DIAGNOSTICS", x, y, color=_ACCENT, bold=True)
    y += _ROW_H

    total_tokens, cost_per_hr = _cost_per_hour()
    _draw_text(screen, f"{total_tokens / 1000:.1f}k TOK  {_format_cost(cost_per_hr)}", x, y)
    y += _ROW_H

    if psutil is not None:
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        _draw_text(screen, f"CPU {cpu:4.1f}%   RAM {ram:4.1f}%", x, y)
    else:
        _draw_text(screen, "CPU/RAM unavailable (pip install psutil)", x, y, color=_DIM)
    y += _ROW_H + 8

    # --- Connections (2026-08-09) ---
    _draw_text(screen, "CONNECTIONS", x, y, color=_ACCENT, bold=True)
    y += _ROW_H
    player_count = len(state.quiz_players)
    _draw_text(screen, f"Quiz clients: {player_count}", x, y,
               color=_TEXT if player_count else _DIM)
    y += _ROW_H
    dmx_ok = dmx.active
    _draw_text(screen, f"DMX: {'CONNECTED' if dmx_ok else 'NO CONNECTION'}", x, y,
               color=_TEXT if dmx_ok else _DIM)
    y += _ROW_H
    _draw_text(screen, f"LED display: {led_bridge.current_transport()}", x, y)
    y += _ROW_H
    _draw_text(screen, f"Tunnel: {_TUNNEL_STATUS_LABELS.get(tunnel_engine.get_status(), '?')}",
               x, y, color=_TEXT if tunnel_engine.get_status() == "live" else _DIM)
    y += _ROW_H + 8

    _draw_text(screen, "AUTO-DJ TRANSITION", x, y, color=_ACCENT, bold=True)
    y += _ROW_H
    remaining = state.auto_dj_track_duration - (t - state.auto_dj_track_started_at)
    remaining = max(0.0, remaining)
    _draw_text(screen, f"T-minus {remaining:5.1f}s", x, y)
    y += _ROW_H
    _draw_button(screen, "autodj_minus10", pygame.Rect(x, y, (w - 8) // 2, 22), "-10s")
    _draw_button(screen, "autodj_plus10", pygame.Rect(x + (w - 8) // 2 + 8, y, (w - 8) // 2, 22), "+10s")

    # --- Column 3: controls ---
    x = _COL3_X
    y = _PAD
    w = _COL3_W
    _draw_checkbox(screen, "suppress_ai_checkbox", x, y, "Suppress AI Functions", state.ai_suppressed)
    y += _ROW_H + 8

    # --- Trivia Night show flow (2026-08-13): touchscreen-tappable
    # equivalent of the web remote's Setup-page "Start Game" and
    # in-show "Stop Game" buttons, for an operator standing at the rig
    # without a phone/browser handy. Both call straight into
    # drivers/show_engine.py, which guards each action against being
    # fired from the wrong phase -- so these are always shown (no
    # layout-shifting show/hide logic) and simply no-op if pressed at a
    # moment they don't apply.
    _draw_text(screen, "SHOW", x, y, color=_ACCENT, bold=True)
    y += _ROW_H
    _draw_text(screen, f"Phase: {state.show_phase.upper()}", x, y, color=_DIM)
    y += _ROW_H
    show_half = (w - 8) // 2
    _draw_button(screen, "show_start_game", pygame.Rect(x, y, show_half, 24), "START GAME")
    _draw_button(screen, "show_stop_game", pygame.Rect(x + show_half + 8, y, show_half, 24),
                 "STOP GAME", danger=True)
    y += 24 + 8

    # --- Playback controls (2026-08-09) -- touchscreen-tappable equivalent
    # of the physical gamepad's volume/next/prev, for the 800x480 Pi screen
    # where a mouse/gamepad may not be within reach.
    _draw_text(screen, "PLAYBACK", x, y, color=_ACCENT, bold=True)
    y += _ROW_H
    title, artist = deck_orchestrator.get_now_playing()
    now_playing = f"{title} - {artist}" if artist else title
    if _font.size(now_playing)[0] > w:
        now_playing = now_playing[:32] + "..."
    _draw_text(screen, now_playing, x, y, color=_DIM)
    y += _ROW_H
    _draw_text(screen, f"VOL {state.music_volume}%", x, y)
    y += _ROW_H
    half = (w - 8) // 2
    _draw_button(screen, "vol_down", pygame.Rect(x, y, half, 24), "VOL -")
    _draw_button(screen, "vol_up", pygame.Rect(x + half + 8, y, half, 24), "VOL +")
    y += 24 + 6
    _draw_button(screen, "prev_track", pygame.Rect(x, y, half, 24), "<< PREV")
    _draw_button(screen, "next_track", pygame.Rect(x + half + 8, y, half, 24), "NEXT >>")
    y += 24 + 8

    _draw_button(screen, "test_westminster", pygame.Rect(x, y, w, 24), "TEST WESTMINSTER CLOCK")
    y += 24 + 8

    _draw_button(screen, "close_app", pygame.Rect(x, y, w, 26), "CLOSE APP", danger=True)
