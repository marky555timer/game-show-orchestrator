# state.py
import time

class State:
    MODE_DJ = 0
    MODE_GAME = 1

    def __init__(self):
        self.mode = self.MODE_DJ
        self.music_volume = 100
        self.status_msg = "SYS OK"
        self.msg_timer = 0
        self.active_option = "A"
        self.active_track = "NO TRACK LOADED"
        
        # Crossfader & Deck Track Storage
        self.deck1_track = ("READY FOR DECK 1", "")
        self.deck2_track = ("READY FOR DECK 2", "")
        self.active_deck = 1  # 1 or 2

        # True once a deck's (title, artist) came from an exact rekordbox
        # DB match or a resolved AI cleanup -- i.e. NOT a raw regex guess.
        self.deck1_confident = False
        self.deck2_confident = False

        # AI-generated "did you know" factoid + quiz content for the
        # currently identified track. factoid_track_key ties this content
        # to a specific track so a deck change doesn't show stale info.
        self.factoid_track_key = ""
        self.factoid_headline = ""
        self.factoid_full = ""
        self.factoid_question = ""
        self.factoid_choices = []          # list of 4 answer strings
        self.factoid_correct_index = -1    # index into factoid_choices
        self.factoid_status = ""           # human-readable reason, console only

        # Volume overlay: panel 6 shows VOL% + bar until this timestamp.
        self.vol_overlay_until = 0.0

        # Quiz mode answer selection.
        self.quiz_selected_index = -1
        self.quiz_locked = False
        self.quiz_graded_at = 0.0  # time.time() when grade_quiz_selection() ran

        # True when the currently-loaded quiz question is the local
        # placeholder (no real AI-sourced question available yet), so
        # the select/grade/DMX flow can still be tested end-to-end.
        self.quiz_is_test = False

        # --- Quiz API gate (Btn6 in DJ mode: fetch, auto-enters on success) ---
        self.quiz_gate_status = "idle"   # idle | fetching
        self.quiz_gate_key = ""
        self.coin_pop_flash_until = 0.0  # panel-3 "out of credits" indicator

        # --- Scoring ---
        self.quiz_score_correct = 0
        self.quiz_score_total = 0
        self.quiz_stats_until = 0.0  # score page shown on panels 1+2 until this time

        # --- DMX Fixture 1 (win/loss indicator lamp) ---
        self.fixture1_mode = "off"  # off | win | loss
        self.fixture1_mode_set_at = 0.0

        # --- DJ-mode uplighting (fixtures 2-11) ---
        self.dj_theme_index = 0
        self.dj_color_index = 0
        self.tempo_tap_times = []
        self.dj_tempo_period = 0.6
        self.tempo_flash_at = 0.0

        # --- Game-mode chase pace override window (win/loss) ---
        self.chase_pace_mode = "mid"   # mid | fast | slow
        self.chase_pace_until = 0.0

        # --- Gamepad button debounce (Btns 5-8) ---
        self.last_button_press_time = {}

        # --- Deck-switch / branding fetch cadence ---
        self.deck_change_count = 0

    def set_status(self, msg, duration=2.0):
        self.status_msg = msg
        self.msg_timer = time.time() + duration

    def set_message(self, msg, duration=2.0):
        self.set_status(msg, duration)

    def toggle_mode(self):
        self.mode = self.MODE_GAME if self.mode == self.MODE_DJ else self.MODE_DJ
        self.set_message("MODE: DJ" if self.mode == self.MODE_DJ else "MODE: GAME")

state = State()