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
        self.now_playing_duration = 0.0  # exact length (sec) of the active deck's track

        # True once a deck's (title, artist) came from an exact rekordbox
        # DB match or a resolved AI cleanup -- i.e. NOT a raw regex guess.
        self.deck1_confident = False
        self.deck2_confident = False

        # How a deck's confident ID was resolved -- "db" | "ai_cache" |
        # "ai_fresh" | "" -- set by drivers/rekordbox_driver.py alongside
        # deckN_confident. Feeds the mobile remote's now-playing status
        # (CACHED/FRESH/UNAVAILABLE), see web/remote_server.py.
        self.deck1_track_source = ""
        self.deck2_track_source = ""

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

        # True/False-only: the actual correct fact, populated whenever the
        # statement's correct answer is "False" so the reveal phase can show
        # the room what's actually true instead of just "FALSE". Empty for
        # every other question shape (multiple_choice, price game, etc.).
        self.factoid_correction = ""
        # Category tag of the CURRENTLY active question (e.g.
        # "identify_band", "era", "true_false") -- lets grading logic key
        # off question type without depending on other state that can have
        # already moved on by grading time (e.g. state.mystery_active).
        self.factoid_category = ""

        # --- Multiplayer QR quiz (2026-08-09) ---
        # player_id -> {"initials": str, "selected_index": int, "locked": bool,
        # "score": int, "locked_at": float}. "locked_at" (2026-08-11) is the
        # timestamp of that player's own /api/player/lock call, used to find
        # who answered first for the mystery-question bonus point.
        # Populated by web/remote_server.py's /api/player/join; empty means
        # "nobody's signed up" -- every consumer (grading, the matrix
        # scorecard) falls back to the original single-shared-answer
        # behavior in that case, so the game works identically whether zero
        # or fifty phones are connected.
        self.quiz_players = {}
        # Bumped every time a NEW question becomes the active one
        # (drivers/factoid_engine.py::_apply_active_question) -- player
        # phones poll this to know when to clear their local selection and
        # show the new question instead of a stale one.
        self.quiz_round_id = 0
        # [{"initials": str, "correct": bool}, ...] for the round just
        # graded -- drives the "who got it right" / "NICE ROUND" scorecard
        # pages in graphics/matrix_canvas.py.
        self.quiz_last_round_results = []

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

        # --- DMX Fixture 1 (win/loss indicator lamp, also joins the idle
        # DJ-mode lightshow -- drivers/lighting_engine.py) ---
        self.fixture1_mode = "off"  # off | win | loss | idle
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
        self.lighting_transition_started_at = 0.0  # 0 = no song-transition fade in progress
        # How long the song-intro BLACK stage holds for THIS transition.
        # Per-transition rather than a constant: an announced transition
        # holds black for the whole station VO so the twinkles fade up as
        # the voice-over ends, while a plain crossfade just uses
        # config.DMX_SONG_INTRO_BLACK_SECONDS.
        # (drivers/lighting_engine.py::trigger_song_transition)
        self.lighting_black_hold_seconds = 0.0
        # True between a song transition starting and the new track's look
        # being decided. The look is only chosen once -- from saved per-track
        # prefs if this track has any, otherwise randomly -- so the room sees
        # exactly one color/pattern change per song instead of a random pick
        # that a recall then immediately overrides.
        # (drivers/lighting_engine.py::trigger_song_transition)
        self.lighting_look_pending = False
        # Set once the operator touches tap-tempo for the current track.
        # Blocks a late-arriving online BPM from yanking the tempo out from
        # under a hand-dialed one. Cleared on every new track.
        self.tempo_operator_set = False
        # Mystery Band lighting sting (drivers/lighting_engine.py).
        self.mystery_light_flash_at = 0.0   # 0 = sting not running
        self.mystery_light_holding = False  # True = parked in blackout, awaiting the teaser
        self.mystery_light_release_at = 0.0  # when the fade back into the pattern began

        # --- Game-mode chase pace override window (win/loss) ---
        self.chase_pace_mode = "mid"   # mid | fast | slow
        self.chase_pace_until = 0.0

        # --- Gamepad button debounce (Btns 5-8) ---
        self.last_button_press_time = {}

        # --- Deck-switch / branding fetch cadence ---
        self.deck_change_count = 0

        # --- 70s/80s "Price Game" bonus round ---
        # Two-line Price Game banner: the abbreviated item ("Doz. Eggs") and
        # the when-line ("in 1984?"). Set by factoid_engine on every applied
        # question -- blank for non-price ones, which is how
        # graphics/matrix_canvas.py falls back to the normal layout.
        self.price_game_item = ""
        self.price_game_when = ""
        self.price_game_pending = False   # armed for the current track, waiting on Btn6
        self.price_game_decade = ""       # e.g. "70s", "90s", "2010s", "2020s"
        # The current track's AI-tagged release year (int) or None. Unlike
        # price_game_decade/pending this is NOT once-per-track-gated -- it
        # just mirrors whatever year is known for whatever's playing, so
        # Btn6 can ask drivers/price_bank_engine.py for a price question
        # from that same year (falling outward to the nearest year that
        # still has unused questions).
        self.track_release_year = None
        self.price_game_active = False    # True during the strobe/banner/question-wait intro
        self.price_game_phase = ""        # "strobe" | "banner" | "question_wait" | ""
        self.price_game_phase_started_at = 0.0
        # Btn6 (drivers/price_bank_engine.py, always-works local CSV path):
        # the drawn question sits here from the instant Btn6 is pressed
        # (synchronous, no fetch delay) until the "question_wait" phase ends
        # and applies it -- None means this round came from the AI-fetch
        # path instead (drivers/price_game_engine.py::update()).
        self.price_game_bank_question = None

        # --- Price Game Mode: background music + MIDI fader duck (Section 1) ---
        # Spans the whole round (intro + question + grading), independent of
        # price_game_active (which only covers the strobe/banner/question_wait
        # intro above) -- see drivers/price_game_engine.py.
        self.price_game_audio_active = False
        self.price_game_audio_entered_game = False  # True once MODE_GAME was seen this round
        self.price_game_audio_started_at = 0.0

        # Session-wide occurrence count: the round-robin index into
        # PRICE_GAME_CATEGORIES so consecutive rounds don't keep landing on
        # the same product type.
        self.price_game_occurrences = 0

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

        # --- Game SFX (ding/buzzer/bigwin/coin/buzz_short) admin toggle ---
        self.sfx_enabled = config.SFX_ENABLED_BY_DEFAULT

        # --- Mystery-teaser defer point (drivers/deck_orchestrator.py sets
        # this on an announced transition; drivers/mystery_band_engine.py
        # waits for it before arming the "Who is this?" teaser) ---
        self.mystery_defer_until = 0.0

        # --- Announcement tag-phrase banner (top 2 panels, during a
        # station announcement VO's dark board-wipe) ---
        self.announcement_banner_from = 0.0
        self.announcement_banner_until = 0.0
        # Basename of the audio/announcements/ file that most recently
        # played -- drivers/announcement_engine.py's text table is keyed by
        # this, and the admin "Last Announcement Text" field reads/writes
        # whichever entry this currently points to (drivers/deck_orchestrator.py
        # sets it the instant that file is picked to play).
        self.last_announcement_filename = ""
        # Forces the banner to show THIS exact text instead of looking up
        # last_announcement_filename's per-file caption -- used for the
        # Trivia Night show flow's first-song sweeper-only transition
        # (drivers/deck_orchestrator.py::trigger_sweeper_only_track_move()),
        # which has no announcement file at all to key a caption off of.
        # "" means "no override, use the normal per-file lookup" -- cleared
        # by every real announced transition so a stale override can never
        # leak onto a later real announcement's banner.
        self.announcement_banner_text_override = ""

        # --- Idle-matrix content cycle: START GAME prompt -> leaderboard ->
        # dancing animations, panels 3-6 in DJ mode (drivers/idle_cycle_engine.py) ---
        self.idle_cycle_phase = "start_game"   # "start_game" | "leaderboard" | "goal" | "dancing"
        self.idle_cycle_phase_started_at = 0.0
        self.idle_cycle_leaderboard_page = 0
        self.idle_cycle_goal_page = 0
        # Which per-theme animation set (graphics/animations.py::IDLE_THEMES)
        # layers on top of the general pool for the "dancing" phase.
        # Operator-selectable in the admin panel; see
        # web/remote_server.py::/api/idle/theme.
        self.idle_theme = config.IDLE_THEME_DEFAULT

        # --- Era-trivia once-per-game scheduling (drivers/factoid_engine.py) ---
        # game_active tracks whether we're mid-game (reset False whenever
        # game mode is exited back to DJ mode, or after a win); the next
        # question served after that starts a fresh count.
        self.game_active = False
        self.game_question_count = 0
        self.era_question_used_this_game = False
        # Displayed on the matrix idle GOAL PAGE ("ROUND N") -- incremented
        # each time a new game starts after a completed win
        # (inputs/gamepad.py::_maybe_reset_after_win()). Starts at 1 for
        # the first game of the session, not 0.
        self.game_round_number = 1

        # --- Live round clock: 30s timeout + auto-grade-when-all-answered
        # (drivers/live_round_engine.py), and teaser-decoupled client
        # answering (drivers/mystery_band_engine.py) ---
        self.round_deadline_at = 0.0
        self.round_timeout_seconds = 0.0
        self.round_timed_out = False
        self.round_first_answer_at = 0.0
        self.round_winner_initials = []
        # player_id of whoever earned the mystery-question first-correct
        # bonus this round ("" most of the time -- only set on the
        # identify_band question, cleared every _grade_multiplayer_round()).
        self.round_bonus_player_id = ""

        # --- Win sequence: duck music, applause, restore, then reset on
        # next game entry (drivers/win_sequence_engine.py) ---
        self.game_win_score = config.GAME_WIN_SCORE  # operator-editable (web/static/index.html)
        self.game_winner_player_id = ""
        self.win_sequence_active = False
        self.win_sequence_phase = ""     # "duck" | "applause" | "restore"
        self.win_sequence_started_at = 0.0
        self.win_applause_ends_at = 0.0

        # --- Post-win intermission (drivers/win_sequence_engine.py starts
        # this the instant the duck+applause+restore sequence finishes) ---
        self.intermission_minutes = config.INTERMISSION_MINUTES_DEFAULT  # operator-editable
        self.intermission_active = False
        self.intermission_ends_at = 0.0
        # Web remote Pause/"set remaining time" controls (2026-08-12), for
        # the "crowd's taking too long to get ready" case. While paused, the
        # countdown freezes at intermission_paused_remaining instead of
        # ticking down against intermission_ends_at -- see
        # drivers/win_sequence_engine.py::get_intermission_remaining_seconds(),
        # the single source of truth every display/check reads through.
        self.intermission_paused = False
        self.intermission_paused_remaining = 0.0

        # --- Btn1 hold: confidence/availability status overlay (panel 3) ---
        # True for as long as Btn1 is physically held past the tap/hold
        # threshold; on release it goes False but overlay_until keeps the
        # icon showing for BTN1_HOLD_OVERLAY_PERSIST_SECONDS more, so the
        # render check is `btn1_hold_overlay_active or t < btn1_hold_overlay_until`.
        self.btn1_hold_overlay_active = False
        self.btn1_hold_overlay_until = 0.0

        # --- Btn3 hold: session AI token-usage overlay (panel 3) ---
        # True strictly while Btn3 is held past the threshold -- hides
        # immediately on release, no persistence window (unlike Btn1 above).
        self.btn3_token_overlay_active = False

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

        # --- Web remote virtual D-pad (no-hardware-gamepad fallback) ---
        # Mirrors a physical held direction: inputs/gamepad.py's
        # _held_space_invaders_direction() treats this the same as a held
        # key/hat/axis for as long as it hasn't expired. The remote's
        # touchstart/touchend handlers set/clear it directly, and also
        # re-POST every couple hundred ms while held as a dead-man's-switch
        # so a dropped connection can't leave the cannon drifting forever.
        self.web_si_direction = 0
        self.web_si_direction_expires_at = 0.0

        # --- Master AI Suppress checkbox (Feature Update, desktop overlay
        # panel): when True, blocks new AI cleanup / trivia-prefetch / Price
        # Game question requests. Cached/local content keeps working since
        # it costs nothing. Default OFF (unchecked). ---
        self.ai_suppressed = False

        # --- Westminster "Bat Clock" top-of-hour event (Feature Update) ---
        # See drivers/westminster_engine.py for the phase machine.
        self.westminster_active = False
        self.westminster_phase = ""  # "" | "flash" | "particles" | "readout"
        self.westminster_phase_started_at = 0.0
        # Sequence-wide clock (independent of the per-phase timer above) --
        # drives the DMX kill/strobe/UV-ramp sub-sequence, whose timing
        # doesn't need to line up with the matrix phase durations.
        self.westminster_started_at = 0.0
        self.westminster_strobe_count = 0  # randomized 1-3 per sequence
        # Volume snapshot taken before the audio duck, so it can be
        # restored exactly once the chime finishes (Auto Voice ON only).
        self.westminster_pre_mute_volume = 100
        self.westminster_chime_ends_at = 0.0
        # -1 sentinel: no hour fired yet this session. Set to the local
        # hour (0-23) the instant the automatic top-of-hour trigger fires,
        # so it can't re-fire again within the same hour.
        self.westminster_last_fired_hour = -1
        # [{"x","y","vx","vy"}, ...] -- lightweight particle scatter for the
        # "particles" phase, dealt once on phase entry (same style as the
        # existing Space Invaders si_bullets/si_explosions state above).
        self.westminster_particles = []

        # --- Trivia Night Show Flow (2026-08-13): Setup -> Countdown ->
        # scripted open -> live show -> scripted close. Sits ABOVE self.mode
        # (DJ/GAME/SPACE_INVADERS), which keeps working unmodified during
        # "live" -- this only owns the bookends. See drivers/show_engine.py.
        self.show_phase = "setup"  # "setup" | "countdown" | "intro" | "live" | "outro"
        self.show_phase_started_at = 0.0
        # Epoch timestamp for a scheduled "Start Game at 7PM" -- 0 means no
        # scheduled start is armed (operator used "Start Game" immediately,
        # or hasn't started anything yet).
        self.show_scheduled_start_at = 0.0
        # Set once, when the outro begins: the champion banner text (e.g.
        # "ABC WINS TRIVIA NIGHT!", possibly multiple tied initials joined
        # "&", or a generic thanks-for-playing line if nobody scored) and
        # the epoch timestamp ShowEnd.mp3 is expected to finish -- both the
        # LED banner-vs-contact-card swap (matrix_canvas.py) and the DMX
        # twinkle-vs-fade-to-black swap (lighting_engine.py) key off the
        # same timestamp so they can never drift out of sync with each other.
        self.show_outro_message = ""
        self.show_outro_music_ends_at = 0.0

        # --- Music-matching filters (Setup page checkboxes) -- wires the
        # music_metadata_engine tags (previously unwired, "Phase 3") into
        # drivers/deck_orchestrator.py's next-track picking for the
        # duration of tonight's show. Untagged tracks pass through
        # unfiltered (permissive default -- only ~90% of the library is
        # tagged; treating "unknown" as "exclude" would silently starve the
        # rotation). Reset to defaults (all False / empty) each time the
        # Setup page is (re)saved.
        self.show_exclude_explicit = False
        self.show_exclude_slow_dance = False
        self.show_exclude_never_charted = False
        self.show_allowed_genres = []  # empty = no genre restriction

        # --- Auto Final Round: the round number, if any, whose win ends the
        # night (outro) instead of starting the normal intermission. Set
        # once at Start Game time from the Setup page's computed round
        # estimate; None if "Auto Final Round" wasn't checked, meaning only
        # the manual STOP GAME button ends the night. See the new branch in
        # drivers/win_sequence_engine.py's win-restore completion.
        self.show_final_round_number = None

        # --- Cumulative night-long scoring (2026-08-13): additive on top of
        # the existing per-round state.quiz_players[...]['score'] (which
        # still resets to 0 every intermission and still drives the
        # existing game_win_score round-win trigger, completely unchanged).
        # player_id -> running total across every round played tonight,
        # accumulated the instant a round's winner is detected (or on a
        # manual mid-round Stop Game) via
        # drivers/win_sequence_engine.py::_accumulate_round_scores(). Reset
        # to {} each time a new show starts. Read by the outro sequence to
        # crown "<INITIALS> WINS TRIVIA NIGHT!" (or a generic thanks-for-
        # playing message if empty, or multiple tied initials joined "&").
        self.show_cumulative_scores = {}

        # --- Application shutdown (Feature Update) ---
        # Set True by any of the three shutdown triggers (web remote button,
        # gamepad Btn5+Btn1 5s hold combo, or the ordinary pygame QUIT/ALT+F4
        # path setting it implicitly via the main loop) -- main.py's game
        # loop checks this every frame and, once True, breaks out to run the
        # single shared graceful-shutdown sequence (cache saves, DMX
        # blackout, driver stop) instead of each trigger duplicating it.
        self.shutdown_requested = False
        self.shutdown_reason = ""

    def set_status(self, msg, duration=2.0):
        self.status_msg = msg
        self.msg_timer = time.time() + duration

    def set_message(self, msg, duration=2.0):
        self.set_status(msg, duration)

    def toggle_mode(self):
        self.mode = self.MODE_GAME if self.mode == self.MODE_DJ else self.MODE_DJ
        self.set_message("MODE: DJ" if self.mode == self.MODE_DJ else "MODE: GAME")

state = State()