import time
import pygame
from config import (
    MATRIX_WIDTH, MATRIX_HEIGHT, PIXEL_SCALE, GAP, WINDOW_W, WINDOW_H,
    RED_FULL, RED_DIM, RED_OFF, BLACK, FONT_5X7
)
from state import state
from drivers.midi_driver import midi_status_str
from drivers.rekordbox_driver import get_rekordbox_track
import audio.audio_engine as ae

# Initialize Pygame Display Engine
screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
pygame.display.set_caption("256x64 Game Board & MIDI Telemetry Simulator")

matrix_surface = pygame.Surface((MATRIX_WIDTH, MATRIX_HEIGHT))

def draw_bitmap_text(surface, text, start_x, start_y, color=RED_FULL, invert=False):
    curr_x = start_x
    text = str(text).upper()
    
    for char in text:
        glyph = FONT_5X7.get(char, FONT_5X7[' '])
        for row_idx, row in enumerate(glyph):
            for col_idx, pixel in enumerate(row):
                if pixel == '1':
                    px = curr_x + col_idx
                    py = start_y + row_idx
                    if 0 <= px < MATRIX_WIDTH and 0 <= py < MATRIX_HEIGHT:
                        surface.set_at((px, py), BLACK if invert else color)
        curr_x += 6

def update_matrix_canvas():
    matrix_surface.fill(BLACK)

    # ZONE 1: TOP HEADLINE BANNER
    HEADLINE_START_X = 64
    
    if state.mode == state.MODE_DJ:
        pygame.draw.rect(matrix_surface, RED_FULL, (HEADLINE_START_X, 2, 128, 11))
        draw_bitmap_text(matrix_surface, "* DJ CONSOLE *", HEADLINE_START_X + 22, 4, invert=True)
    else:
        pygame.draw.rect(matrix_surface, RED_FULL, (HEADLINE_START_X, 2, 128, 11))
        draw_bitmap_text(matrix_surface, "* TRIVIA ARENA *", HEADLINE_START_X + 16, 4, invert=True)

    if time.time() < state.msg_timer:
        draw_bitmap_text(matrix_surface, state.status_msg, HEADLINE_START_X + 4, 18, color=RED_FULL)
    else:
        draw_bitmap_text(matrix_surface, midi_status_str, HEADLINE_START_X + 4, 18, color=RED_FULL)

    # ZONE 2: BOTTOM DETAIL CANVAS (Panels 3, 4, 5, 6)
    if state.mode == state.MODE_DJ:
        # Pull active track (title, artist) tuple or string
        track_info = get_rekordbox_track()
        if isinstance(track_info, tuple):
            song_title, artist_name = track_info
        else:
            song_title, artist_name = str(track_info), ""

        # Line 1: Song Title
        draw_bitmap_text(matrix_surface, f"TITLE: {song_title}", 4, 35, color=RED_FULL)
        
        # Line 2: Artist Name
        if artist_name:
            draw_bitmap_text(matrix_surface, f"ARTIST: {artist_name}", 4, 44, color=RED_FULL)
        
        # Volume Level Bar
        vol_w = int((state.music_volume / 100.0) * 230)
        pygame.draw.rect(matrix_surface, RED_DIM, (12, 53, 232, 8), 1)
        pygame.draw.rect(matrix_surface, RED_FULL, (12, 53, vol_w, 8))
        draw_bitmap_text(matrix_surface, f"FASTER VOL: {state.music_volume}% (MIDI CC#11)", 60, 54, invert=True if vol_w > 120 else False)

    else:
        opt_a = "> OPTION A: HOT PINK" if state.active_option == "A" else "  OPTION A: HOT PINK"
        opt_b = "> OPTION B: BABY BLUE" if state.active_option == "B" else "  OPTION B: BABY BLUE"
        opt_c = "> OPTION C: SEA GREEN" if state.active_option == "C" else "  OPTION C: SEA GREEN"

        draw_bitmap_text(matrix_surface, opt_a, 4, 36, color=RED_FULL, invert=(state.active_option == "A"))
        draw_bitmap_text(matrix_surface, opt_b, 4, 48, color=RED_FULL, invert=(state.active_option == "B"))
        draw_bitmap_text(matrix_surface, opt_c, 132, 36, color=RED_FULL, invert=(state.active_option == "C"))
        
        rvb_txt = "AUDIO REVERB: ON" if ae.reverb_enabled else "AUDIO REVERB: OFF"
        draw_bitmap_text(matrix_surface, rvb_txt, 132, 48, color=RED_FULL)

def render_led_grid():
    screen.fill((5, 0, 0))
    for y in range(MATRIX_HEIGHT):
        for x in range(MATRIX_WIDTH):
            color = matrix_surface.get_at((x, y))
            if color == (0, 0, 0, 255):
                color = RED_OFF

            rect = (
                x * PIXEL_SCALE + GAP,
                y * PIXEL_SCALE + GAP,
                PIXEL_SCALE - GAP,
                PIXEL_SCALE - GAP
            )
            pygame.draw.rect(screen, color, rect)

    # Physical Panel Seam Overlay
    pygame.draw.line(screen, (80, 0, 0), (0, 32 * PIXEL_SCALE), (WINDOW_W, 32 * PIXEL_SCALE), 2)
    for px_x in (64, 128, 192):
        pygame.draw.line(screen, (80, 0, 0), (px_x * PIXEL_SCALE, 32 * PIXEL_SCALE), (px_x * PIXEL_SCALE, WINDOW_H), 2)
    pygame.draw.line(screen, (80, 0, 0), (128 * PIXEL_SCALE, 0), (128 * PIXEL_SCALE, 32 * PIXEL_SCALE), 2)

    pygame.display.flip()