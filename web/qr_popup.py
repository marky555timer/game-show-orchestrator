# web/qr_popup.py
"""Always-on-top QR popup for the local web remote (Btn3 tap, DJ mode --
see inputs/gamepad.py::_handle_btn3_release()). Deliberately kept entirely
on the MAIN thread (not a background thread) to sidestep Tkinter's
cross-thread unsafety: init() creates one hidden root at startup, and
pump() -- called once per frame from main.py, right after
process_events() -- drives that same root's event loop. Because
trigger_qr_popup() is called synchronously from gamepad.py's Btn3-tap
handler, which itself runs inside process_events() on the main thread,
every Tk call in this module happens on the same thread throughout, with
no marshaling needed."""
import time

try:
    import tkinter as tk
    from PIL import ImageTk
    import qrcode
except ImportError:
    tk = None
    ImageTk = None
    qrcode = None

import config
from web.net_info import get_admin_url

_root = None
_popup = None
_dismiss_at = 0.0
_photo = None  # keep a reference alive -- Tk drops PhotoImages with no Python ref


def init():
    """Call once from main.py before the game loop starts."""
    global _root
    if tk is None:
        print("[QR POPUP] tkinter/Pillow/qrcode not available -- QR popup disabled.")
        return
    try:
        _root = tk.Tk()
        _root.withdraw()  # only the QR Toplevel is ever actually shown
    except Exception as e:
        print(f"[QR POPUP] Could not initialize Tk root: {e}")
        _root = None


def pump():
    """Call once per frame from main.py, right after process_events(). A
    stray TclError here must never be allowed to kill the show loop."""
    global _popup, _photo
    if _root is None:
        return
    try:
        if _popup is not None and time.time() >= _dismiss_at:
            _close_popup()
        _root.update_idletasks()
        _root.update()
    except Exception as e:
        print(f"[QR POPUP] pump() error (ignored): {e}")


def _close_popup():
    global _popup, _photo
    if _popup is not None:
        try:
            _popup.destroy()
        except Exception:
            pass
    _popup = None
    _photo = None


def trigger_qr_popup():
    """Btn3 tap: opens the QR popup, or -- if one is already open -- closes
    it immediately (second-tap dismiss)."""
    global _popup, _photo, _dismiss_at
    if _root is None or qrcode is None or ImageTk is None:
        print("[QR POPUP] Cannot show QR popup -- tkinter/Pillow/qrcode not available.")
        return

    if _popup is not None:
        _close_popup()
        return

    url = get_admin_url()
    try:
        img = qrcode.make(url)
        _photo = ImageTk.PhotoImage(img.convert("RGB"), master=_root)

        popup = tk.Toplevel(_root)
        popup.title("Scan for Web Remote")
        popup.attributes("-topmost", True)
        popup.resizable(False, False)

        tk.Label(popup, image=_photo).pack(padx=12, pady=(12, 4))
        tk.Label(popup, text=url, font=("Consolas", 11)).pack(padx=12, pady=(0, 12))

        popup.update_idletasks()
        sw, sh = popup.winfo_screenwidth(), popup.winfo_screenheight()
        w, h = popup.winfo_width(), popup.winfo_height()
        popup.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        _popup = popup
        _dismiss_at = time.time() + config.QR_POPUP_AUTO_DISMISS_SECONDS
        print(f"[QR POPUP] Showing remote URL: {url}")
    except Exception as e:
        print(f"[QR POPUP] Failed to build/show QR popup: {e}")
        _popup = None
        _photo = None
