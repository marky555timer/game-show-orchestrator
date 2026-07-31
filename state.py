# state.py
import time
import config

class State:
    MODE_DJ = 0
    MODE_GAME = 1
    MODE_SPACE_INVADERS = 2

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

        # AI-generated "did you know" factoid preview + AI pipeline status for
        # the currently buffered track. factoid_track_key ties this content
        # to a specific track so a deck change doesn't show stale info. This
        # mirrors track_question_queue[0] (peek only) -- it drives the DJ-mode
        # panel3 status indicator and optional top-page factoid display, and
        # is separate from the ACTIVE round question below.
        self.factoid_track_key = ""
        self.factoid_headline = ""
        self.factoid_full = ""
        self.factoid_status = ""           # human-readable reason, console only

        # The ACTIVE on-screen round question -- only populated when a
        # question is actually pulled into play (Btn6 pop, multi-question
        # auto-advance, or an offline fallback), not merely prefetched.
        self.factoid_question = ""
        self.factoid_choices = []          # list of 4 answer strings
        self.factoid_correct_index = -1    # index into factoid_choices

        # Background pre-fetch queue (Section 1): up to TRACK_QUESTIONS_PER_TRACK
        # question dicts, pre-fetched for factoid_track_key and not yet asked
        # this session. Btn6 and the multi-question auto-advance loop both pop
        # from this list. drivers/factoid_engine.py owns writing to it.
        self.track_question_queue = []

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
        self.quiz_gate_started_at = 0.0  # time.time() when the Btn6 fetch was kicked off
        self.coin_pop_flash_until = 0.0  # panel-3 "out of credits" indicator

        # --- Scoring ---
        self.quiz_score_correct = 0
        self.quiz_score_total = 0
        self.quiz_stats_until = 0.0  # score page shown on panels 1+2 until this time

        # --- DMX Fixture 1 (win/loss indicator lamp) ---
        self.fixture1_mode = "off"  # off | win | loss
        self.fixture1_mode_set_at = 0.0

        # --- DMX amplified grade feedback: brief ALL-fixture strobe pulse
        # (fixtures 2-11) the instant an answer is graded, alongside
        # Fixture 1's own latched win/loss lamp above.
        self.fixture_flash_mode = ""   # "" | "win" | "loss"
        self.fixture_flash_until = 0.0

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

        # --- 70s/80s "Price Game" bonus round ---
        self.price_game_pending = False   # armed for the current track, waiting on Btn6
        self.price_game_decade = ""       # "70s" | "80s"
        self.price_game_active = False    # True during the strobe/banner/question-wait intro
        self.price_game_phase = ""        # "strobe" | "banner" | "question_wait" | ""
        self.price_game_phase_started_at = 0.0

        # --- Price Game Mode: background music + MIDI fader duck (Section 1) ---
        # Spans the whole round (intro + question + grading), independent of
        # price_game_active (which only covers the strobe/banner/question_wait
        # intro above) -- see drivers/price_game_engine.py.
        self.price_game_audio_active = False
        self.price_game_audio_entered_game = False  # True once MODE_GAME was seen this round
        self.price_game_audio_started_at = 0.0

        # Session-wide occurrence count (Section 1 music rule): the first
        # Price Game this session plays its background bed in full;
        # subsequent ones cap at PRICE_GAME_MUSIC_REPEAT_CAP_SECONDS. Also
        # doubles as the round-robin index into PRICE_GAME_CATEGORIES.
        self.price_game_occurrences = 0
        self.price_game_audio_is_first = True
        self.price_game_audio_capped = False  # True once the repeat-cap early fade has fired

        # --- Price Game Mode: product repetition cooldown ---
        # List of {"product": str, "round": int} entries, oldest first --
        # "round" is a snapshot of quiz_score_total (trivia rounds graded
        # this session) at the moment the product was asked. A product is
        # excluded from the AI prompt's candidate pool while
        # quiz_score_total - entry["round"] < config.PRICE_GAME_PRODUCT_COOLDOWN_ROUNDS.
        # See drivers/price_game_engine.py.
        self.price_game_product_history = []

        # --- DJ mode "Mystery Band" teaser (Section 2) ---
        # Artist keys (lowercased) already asked about via a real question
        # this session -- drivers/factoid_engine.py._apply_active_question()
        # populates this every time a question actually gets served.
        self.asked_artists = set()

        self.mystery_active = False        # teaser or reveal-blink in progress
        self.mystery_resolved = False       # True once the 10s window has expired unanswered
        self.mystery_started_at = 0.0
        self.mystery_track_key = ""         # factoid_track_key this mystery was armed for
        self.mystery_artist_key = ""        # normalized artist key for the current mystery
        self.mystery_artist_display = ""    # original-case artist name, for the identify question
        self.mystery_identify_question = None  # prebuilt "who is this" question dict, or None
        self.mystery_reveal_until = 0.0     # reveal-blink window end (set once resolved)

        # --- Auto-DJ (Section 4): track-length auto-advance ---
        self.auto_dj_enabled = config.AUTODJ_ENABLED_BY_DEFAULT
        self.auto_dj_track_key = ""             # mirrors factoid_track_key once confidently identified
        self.auto_dj_track_started_at = 0.0
        self.auto_dj_track_duration = config.AUTODJ_DEFAULT_TRACK_SECONDS
        self.auto_dj_overlay_text = ""          # "AUTO ON" / "AUTO OFF", panel 3
        self.auto_dj_overlay_until = 0.0

        # --- Auto-DJ: overlapping station-announcement transition ---
        self.auto_dj_announcement_played = False  # True once this cycle's VO has fired
        self.auto_dj_transition_at = 0.0           # scheduled time to fire the deck-start+TrackSearch transition (0 = not scheduled)

        # --- Auto-Announcement toggle: Gamepad Btn1, DJ mode only ---
        self.auto_announce_enabled = config.AUTO_ANNOUNCE_ENABLED_BY_DEFAULT
        self.auto_announce_overlay_text = ""    # "v ON" / "v OFF", panel 3
        self.auto_announce_overlay_until = 0.0

        # --- Space Invaders mini-game (DJ-mode Btn1+Btn3 combo Easter egg) ---
        # Entered/exited/simulated by drivers/space_invaders_engine.py,
        # rendered by graphics/matrix_canvas.py::_render_space_invaders.
        self.si_player_x = 0.0
        self.si_bullets = []          # [{"x","y"}, ...] player projectiles, traveling up
        self.si_invaders = []         # [{"x","y","alive"}, ...] formation grid
        self.si_explosions = []       # [{"x","y","started_at"}, ...] brief hit-flash markers
        self.si_direction = 1         # formation sweep direction: 1 right, -1 left
        self.si_last_tick_at = 0.0
        self.si_tick_interval = config.SI_INVADER_TICK_START_SECONDS
        self.si_last_fire_at = 0.0
        self.si_score = 0
        self.si_wave = 1

    def set_status(self, msg, duration=2.0):
        self.status_msg = msg
        self.msg_timer = time.time() + duration

    def set_message(self, msg, duration=2.0):
        self.set_status(msg, duration)

    def toggle_mode(self):
        self.mode = self.MODE_GAME if self.mode == self.MODE_DJ else self.MODE_DJ
        self.set_message("MODE: DJ" if self.mode == self.MODE_DJ else "MODE: GAME")

state = State()