"""Set the admin password (2026-10-18) -- one password for the web Show
Designer (triviacade.com/play/designer/) and the show machine's admin pages.

Run it yourself in a terminal (the password is typed, never shown or saved
in plain text):   python tools/set_admin_password.py

Only a PBKDF2-SHA256 hash goes to triviacade.com; the Pi picks it up on its
next sync (drivers/web_sync.py). Setting a new one logs every browser out.
"""
import getpass
import hashlib
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

ITERATIONS = 310000


def main():
    pw = getpass.getpass("New admin password: ")
    if len(pw) < 6:
        sys.exit("Use at least 6 characters.")
    if getpass.getpass("Again: ") != pw:
        sys.exit("They don't match -- nothing changed.")
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITERATIONS, dklen=32).hex()
    r = requests.post(f"{config.WEB_DESIGNER_API_URL}?a=pi/set-password",
                      json={"salt": salt.hex(), "iter": ITERATIONS, "hash": h},
                      headers={"X-Trivia-Key": config.TUNNEL_REDIRECT_SECRET}, timeout=20)
    if r.status_code != 200 or not r.json().get("ok"):
        sys.exit(f"triviacade.com refused it: HTTP {r.status_code} {r.text[:200]}")
    print("Password set. Log in at", config.WEB_DESIGNER_URL)
    print("The show machine picks it up on its next sync (within ~2 minutes of being on).")


if __name__ == "__main__":
    main()
