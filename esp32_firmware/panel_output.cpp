#include "panel_output.h"

// Pin/panel config is defined here, in the SAME translation unit as
// `display`, deliberately -- C++ only guarantees global-initializer order
// WITHIN one file. It used to live in a separate config.cpp, which left
// `display`'s constructor (it copies its DisplayConfig argument by value)
// free to run before `cfg` was necessarily initialized -- undefined
// cross-TU order -- silently zeroing out every pin while display.begin()
// still reported success. That's why QuadrantTest.ino (everything in one
// file, order guaranteed) worked while this didn't.
static uint8_t oePin = 22;
static uint8_t clkPin = 18;
static uint8_t latPin = 2;

static uint8_t aPin = 19;
static uint8_t bPin = 21;

static uint8_t rDataPin = 23;

static uint8_t addressPins[] = { aPin, bPin };
static uint8_t dataPins[] = { rDataPin };

static DisplayConfig cfg = {
  PANEL_WIDTH, PANEL_HEIGHT,  // panel
  oePin, clkPin, latPin,      // control
  addressPins, 2,             // address
  dataPins, 1                 // data
};

FthnLabsDisplay display(cfg);
GFXcanvas1 canvas(PANEL_WIDTH, PANEL_HEIGHT);

// 3-row (6-panel) layout. Logical quadrants, drawn normally in top-left
// origin coordinates:
//   [A][B]
//   [C][D]
//   [E][F]
//
// Chain order (nearest ESP -> farthest): TR -> TL -> BL(C) -> BR(D) -> F -> E
// Growing the chain past D shifts which raw buffer slot every panel reads
// from (the nearest panel always gets whatever is sent *last*), so this
// mapping was recalculated from scratch for 6 panels. Confirmed against
// hardware with QuadrantTest.ino (v3).
struct QuadMap {
  uint16_t srcX, srcY; // logical (correct) quadrant origin
  uint16_t dstX, dstY; // raw draw-buffer quadrant origin that feeds that panel
  bool rotate180;
};

static const uint16_t kQW = PANEL_WIDTH / 2;   // 32
static const uint16_t kQH = PANEL_HEIGHT / 3;  // 16 (each panel is 32x16, 3 rows of panels)

static const QuadMap kQuadMap[6] = {
  { 0 * kQW, 0 * kQH,  0 * kQW, 2 * kQH, false }, // A: top-left,  physical TL
  { 1 * kQW, 0 * kQH,  1 * kQW, 2 * kQH, false }, // B: top-right, physical TR
  { 0 * kQW, 1 * kQH,  1 * kQW, 1 * kQH, true  }, // C: mid-left,  physical BL (rotated)
  { 1 * kQW, 1 * kQH,  0 * kQW, 1 * kQH, true  }, // D: mid-right, physical BR (rotated)
  { 0 * kQW, 2 * kQH,  0 * kQW, 0 * kQH, false }, // E: bottom-left,  physical row3-left
  { 1 * kQW, 2 * kQH,  1 * kQW, 0 * kQH, false }, // F: bottom-right, physical row3-right
};

void blitToPanels() {
  for (uint8_t q = 0; q < 6; q++) {
    const QuadMap &m = kQuadMap[q];
    for (uint16_t y = 0; y < kQH; y++) {
      for (uint16_t x = 0; x < kQW; x++) {
        uint16_t val = canvas.getPixel(m.srcX + x, m.srcY + y);
        uint16_t dx, dy;
        if (m.rotate180) {
          dx = m.dstX + (kQW - 1 - x);
          dy = m.dstY + (kQH - 1 - y);
        } else {
          dx = m.dstX + x;
          dy = m.dstY + y;
        }
        display.drawPixel(dx, dy, val);
      }
    }
    // Give the 1ms persistence-of-vision scan interrupt a chance to run
    // between panels -- centralized here since every caller (LOADING
    // screen, live orchestrator frames) goes through this function, and
    // this ~3000-pixel-op loop is exactly the kind of unbroken CPU hog
    // that starved the scan and caused visible flicker/dimming before.
    display.loop();
  }
}
