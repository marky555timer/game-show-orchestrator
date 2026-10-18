"""Copy the Show Designer + preview pages into the triviacade-site repo (2026-10-18).

The Pi serves web/static/designer.html and web/static/show_sim.html itself;
triviacade.com/play/designer/ serves copies of the same two files (app.html,
sim.html) behind its login. This copies them into a local clone of the site
repo; commit and push there (Hostinger deploys a push within seconds).

    python tools/publish_web_designer.py <path to triviacade-site clone>
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = {"designer.html": "app.html", "show_sim.html": "sim.html"}


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    dest = os.path.join(sys.argv[1], "play", "designer")
    if not os.path.isfile(os.path.join(dest, "api.php")):
        sys.exit(f"{dest} doesn't look like the site repo's play/designer folder.")
    for src, name in FILES.items():
        shutil.copyfile(os.path.join(ROOT, "web", "static", src), os.path.join(dest, name))
        print(f"web/static/{src} -> play/designer/{name}")


if __name__ == "__main__":
    main()
