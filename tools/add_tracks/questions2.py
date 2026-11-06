"""Add Tracks 2 questions: merges the batch files questions2_b*.py (keyed by the titles in specs2.py)."""
import glob
import importlib
import os

Q, NO_YEAR = {}, set()
for _p in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "questions2_b*.py"))):
    _m = importlib.import_module(os.path.splitext(os.path.basename(_p))[0])
    Q.update(_m.Q)
    NO_YEAR |= _m.NO_YEAR
