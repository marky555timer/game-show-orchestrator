import time

import config
from state import state
from audio.audio_engine import (
    play_processed_sound, raw_coin, si_tick_sound, si_laser_sound, si_explosion_sound,
    stop_all_arcade_audio,
)

# ==========================================
# SPACE INVADERS MINI-GAME
# ==========================================
# DJ-mode Easter egg: holding Gamepad Button 1 AND Button 3 simultaneously
# enters this mode (see inputs/gamepad.py's JOYBUTTONDOWN combo check);
# Button 7 or Button 8 exits it immediately, straight back to DJ_MODE.
# Rendering lives in graphics/matrix_canvas.py::_render_space_invaders --
# this module owns only the simulation (state.si_* fields) and the
# entry/exit lifecycle. Movement is driven every frame while a
# left/right control is held (inputs/gamepad.py::_process_space_invaders_movement
# -> move_player()); firing is a discrete per-press action (fire()).

_player_top_y = None  # lazily computed, config-derived, never changes at runtime
_last_update_at = None


def _player_top():
    global _player_top_y
    if _player_top_y is None:
        _player_top_y = config.MATRIX_HEIGHT - config.SI_PLAYER_Y_FROM_BOTTOM - config.SI_PLAYER_HEIGHT
    return _player_top_y


def _formation_width():
    return (config.SI_INVADER_COLS - 1) * config.SI_INVADER_SPACING_X + config.SI_INVADER_SIZE


def _spawn_wave():
    start_x = max(0, (config.MATRIX_WIDTH - _formation_width()) // 2)
    invaders = []
    for row in range(config.SI_INVADER_ROWS):
        for col in range(config.SI_INVADER_COLS):
            invaders.append({
                "x": float(start_x + col * config.SI_INVADER_SPACING_X),
                "y": float(config.SI_INVADER_TOP_Y + row * config.SI_INVADER_SPACING_Y),
                "alive": True,
            })
    state.si_invaders = invaders
    state.si_direction = 1
    state.si_tick_interval = config.SI_INVADER_TICK_START_SECONDS
    state.si_last_tick_at = time.time()


def enter_space_invaders():
    """Dual-button entry hook (inputs/gamepad.py): Gamepad Button 1 AND
    Button 3 held simultaneously while in DJ_MODE."""
    global _last_update_at
    state.mode = state.MODE_SPACE_INVADERS
    state.si_player_x = (config.MATRIX_WIDTH - config.SI_PLAYER_WIDTH) / 2.0
    state.si_bullets = []
    state.si_explosions = []
    state.si_score = 0
    state.si_wave = 1
    state.si_last_fire_at = 0.0
    _spawn_wave()
    _last_update_at = None
    play_processed_sound(raw_coin, volume=config.SI_ENTRY_SOUND_VOLUME)
    print("GAME MODE TRANSITION: Space Invaders initialized via Buttons 1+3")


def exit_space_invaders():
    """Btn7/Btn8 hook (inputs/gamepad.py): immediately halts the game loop,
    kills any active arcade SFX (no fade), clears mini-game sprites, and
    returns to DJ_MODE -- normal DJ visuals/lighting resume on the very
    next frame since both graphics/matrix_canvas.py and
    drivers/lighting_engine.py render purely off state.mode."""
    stop_all_arcade_audio()
    state.si_invaders = []
    state.si_bullets = []
    state.si_explosions = []
    state.mode = state.MODE_DJ
    print("SPACE INVADERS EXIT: Returned to DJ Mode via Button 7/8")


def move_player(direction, dt):
    """direction: -1 left, +1 right, 0 idle. Called every frame while a
    movement control is held (inputs/gamepad.py::
    _process_space_invaders_movement)."""
    if direction == 0:
        return
    state.si_player_x += direction * config.SI_PLAYER_SPEED * dt
    state.si_player_x = max(0.0, min(config.MATRIX_WIDTH - config.SI_PLAYER_WIDTH, state.si_player_x))


def fire():
    """Action-button hook (inputs/gamepad.py): spawns a projectile from the
    cannon's current position, rate-limited by SI_FIRE_COOLDOWN_SECONDS so
    holding the button down doesn't spam bullets every frame."""
    now = time.time()
    if now - state.si_last_fire_at < config.SI_FIRE_COOLDOWN_SECONDS:
        return
    state.si_last_fire_at = now
    bullet_x = state.si_player_x + config.SI_PLAYER_WIDTH / 2.0
    state.si_bullets.append({"x": bullet_x, "y": float(_player_top() - 1)})
    play_processed_sound(si_laser_sound, volume=0.8)


def _step_formation():
    alive = [inv for inv in state.si_invaders if inv["alive"]]
    if not alive:
        return

    min_x = min(inv["x"] for inv in alive)
    max_x = max(inv["x"] for inv in alive) + config.SI_INVADER_SIZE

    hit_edge = (
        (state.si_direction > 0 and max_x + config.SI_INVADER_STEP_X > config.MATRIX_WIDTH) or
        (state.si_direction < 0 and min_x - config.SI_INVADER_STEP_X < 0)
    )

    if hit_edge:
        for inv in alive:
            inv["y"] += config.SI_INVADER_DROP_Y
        state.si_direction *= -1
    else:
        dx = config.SI_INVADER_STEP_X * state.si_direction
        for inv in alive:
            inv["x"] += dx

    play_processed_sound(si_tick_sound, volume=0.5)

    if any(inv["y"] + config.SI_INVADER_SIZE >= _player_top() for inv in alive):
        print("[SPACE INVADERS] Formation reached the cannon -- resetting wave.")
        _spawn_wave()


def _rescale_tick_speed():
    """Classic difficulty ramp: fewer invaders left standing -> faster
    formation steps."""
    total = config.SI_INVADER_COLS * config.SI_INVADER_ROWS
    alive_count = sum(1 for inv in state.si_invaders if inv["alive"])
    if alive_count <= 0:
        return
    frac = alive_count / float(total)
    span = config.SI_INVADER_TICK_START_SECONDS - config.SI_INVADER_TICK_MIN_SECONDS
    state.si_tick_interval = config.SI_INVADER_TICK_MIN_SECONDS + span * frac


def _update_bullets(dt):
    remaining = []
    for b in state.si_bullets:
        b["y"] -= config.SI_BULLET_SPEED * dt
        if b["y"] < 0:
            continue

        hit = None
        for inv in state.si_invaders:
            if not inv["alive"]:
                continue
            if (inv["x"] <= b["x"] <= inv["x"] + config.SI_INVADER_SIZE and
                    inv["y"] <= b["y"] <= inv["y"] + config.SI_INVADER_SIZE):
                hit = inv
                break

        if hit:
            hit["alive"] = False
            state.si_score += 10
            state.si_explosions.append({"x": hit["x"], "y": hit["y"], "started_at": time.time()})
            play_processed_sound(si_explosion_sound, volume=0.9)
            _rescale_tick_speed()
            continue  # bullet consumed on impact

        remaining.append(b)
    state.si_bullets = remaining


def _prune_explosions(now):
    state.si_explosions = [
        e for e in state.si_explosions if now - e["started_at"] < config.SI_EXPLOSION_HOLD_SECONDS
    ]


def update(now):
    """Per-frame poll, called from inputs/gamepad.py::process_events() only
    while state.mode == MODE_SPACE_INVADERS."""
    global _last_update_at
    if _last_update_at is None:
        _last_update_at = now
    dt = max(0.0, now - _last_update_at)
    _last_update_at = now

    if now - state.si_last_tick_at >= state.si_tick_interval:
        state.si_last_tick_at = now
        _step_formation()

    _update_bullets(dt)
    _prune_explosions(now)

    if state.si_invaders and not any(inv["alive"] for inv in state.si_invaders):
        state.si_wave += 1
        print(f"[SPACE INVADERS] Wave cleared -- starting wave {state.si_wave}.")
        _spawn_wave()
