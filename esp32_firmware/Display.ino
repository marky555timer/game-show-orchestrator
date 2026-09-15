#include "config.h"
#include "panel_output.h"

// !! EXTERNAL DEPENDENCY WARNING (2026-08-10) !!
// This sketch requires a one-line patch to the FthnLabsDisplay library,
// which lives OUTSIDE this folder (Documents/Arduino/libraries/
// FthnLabsDisplay/FthnLabsDisplay.cpp). Reinstalling or updating that
// library will silently revert it and reintroduce a nasty bug.
//
// The patch: FthnLabsDisplay::loop() must set _shouldScan = false before
// calling scan(). As shipped it only *reads* the flag the timer ISR sets
// and never clears it, so after the first timer tick every single
// loop() call re-runs a full scan -- SPI transfer plus a blocking
// delayMicroseconds() of up to 250us, ~700us total -- instead of the
// intended once-per-millisecond. Consequences when unpatched:
//   * The LED refresh rate is governed by however often display.loop()
//     happens to be called rather than by the timer, so brightness
//     visibly pulses as CPU load shifts.
//   * Any code calling display.loop() in a tight loop (this sketch does,
//     once per received serial byte) stalls at ~700us/iteration and the
//     serial path collapses.
//
// Also required: ESP32 Arduino core 2.0.17, NOT 3.x. FthnLabsDisplay uses
// the core-2.x timer API (timerAttachInterrupt with an edge arg,
// timerAlarmWrite/timerAlarmEnable) that 3.x removed.

// Frame packet format (sent by the orchestrator's led_bridge.py, one per
// render tick, over USB serial): 1 magic byte + 6 panels x 64 bytes. Each
// panel is packed 1bpp, row-major, MSB-first, 4 bytes/row (32px wide) x 16
// rows -- in chain-letter order A-F, i.e. the same logical top-left
// origins panel_output.cpp's kQuadMap uses as `src`.
//
// USB serial only (2026-08-19: WiFi/UDP fallback + the in-field WiFi setup
// AP removed entirely) -- this rig and the Pi driving it are permanently
// cabled together, so there's no scenario left where a wireless transport
// is actually needed, and running the WiFi stack alongside serial was the
// prime suspect for a recurring multi-second stall in this sketch's
// loop() (WiFi connect/retry churn blocking everything else, including
// the heartbeat write below, for seconds at a time). See wifi_field_setup.*
// for the removed captive-portal code, kept on disk but no longer called
// from anywhere in this sketch.
//
// A trailing XOR checksum over the panel payload lets a corrupted/
// misaligned frame be treated as "no frame this tick" rather than drawn
// as garbage -- the serial byte stream has no framing of its own, so one
// byte lost to an RX overflow shifts the magic-byte resync onto the wrong
// offset and every window after it decodes as noise until something
// resyncs.
#define FRAME_MAGIC 0xA5
#define FRAME_PANEL_BYTES 64 // 32x16px / 8 bits per byte
#define FRAME_PAYLOAD_SIZE (6 * FRAME_PANEL_BYTES)
#define FRAME_SIZE (1 + FRAME_PAYLOAD_SIZE + 1) // magic + payload + checksum

// Must match led_bridge.py's _SERIAL_BAUD. Far more headroom than the
// ~7.7KB/s a 386-byte frame at 20Hz actually needs -- the bottleneck is
// this CPU's render speed (see the throughput note on serialFrameLoop),
// never the line rate.
#define SERIAL_BAUD 921600

// How long to keep showing the last received frame after the stream goes
// quiet (e.g. the orchestrator app isn't running) before going back to the
// LOADING screen.
#define FRAME_TIMEOUT_MS 2000

// A single distinct byte written back to the Pi every HEARTBEAT_INTERVAL_MS
// once this sketch has actually reached loop() (i.e. finished setup(),
// including display.begin()) -- the Pi's only real proof of life, since a
// successfully-opened serial port only means the USB-serial chip
// enumerated, not that this sketch is actually running. See
// led_bridge.py's _HEARTBEAT_TIMEOUT_S for the read side and its history
// note on why the freshness window there is 4.0s, not something tighter.
#define HEARTBEAT_BYTE 0x5A
#define HEARTBEAT_INTERVAL_MS 250

static const uint16_t kQW = PANEL_WIDTH / 2;  // 32
static const uint16_t kQH = PANEL_HEIGHT / 3; // 16

// Logical top-left origin of each of the 6 panels, in chain-letter order
// A-F -- must match panel_output.cpp's kQuadMap `src` coordinates.
static const uint16_t kPanelSrcX[6] = { 0, kQW, 0, kQW, 0, kQW };
static const uint16_t kPanelSrcY[6] = { 0, 0, kQH, kQH, 2 * kQH, 2 * kQH };

static bool everLive = false;
static unsigned long lastFrameMs = 0;

// Serial frame reception state. Unlike a UDP packet, serial is just a
// continuous byte stream with no built-in framing, so we resync on the
// magic byte ourselves: byte 0 of a new candidate frame must be
// FRAME_MAGIC, and once that's seen every subsequent byte (including ones
// that happen to equal 0xA5) is just accumulated as payload. The trailing
// checksum (see above) is what actually validates the candidate before
// it's applied -- a checksum failure means this window was misaligned
// garbage, and we just go back to scanning byte-by-byte for the next
// 0xA5, which self-corrects onto the real next frame boundary as soon as
// it arrives (same "at most one bad tick" tolerance the UDP path has).
static uint8_t serialFrameBuf[FRAME_SIZE];
static uint16_t serialFrameIdx = 0;
static uint8_t pendingFrame[FRAME_SIZE];

static uint8_t frameChecksum(const uint8_t *buf) {
  uint8_t sum = 0;
  for (uint16_t i = 1; i <= FRAME_PAYLOAD_SIZE; i++) {
    sum ^= buf[i];
  }
  return sum;
}

static void applyFrame(const uint8_t *buf) {
  const uint8_t *panels = buf + 1; // skip magic byte
  for (uint8_t panel = 0; panel < 6; panel++) {
    const uint8_t *panelBytes = panels + panel * FRAME_PANEL_BYTES;
    uint16_t ox = kPanelSrcX[panel];
    uint16_t oy = kPanelSrcY[panel];
    for (uint16_t y = 0; y < kQH; y++) {
      const uint8_t *row = panelBytes + y * 4; // 32px / 8 = 4 bytes/row
      for (uint16_t x = 0; x < kQW; x++) {
        uint8_t byte = row[x / 8];
        uint8_t bit = (byte >> (7 - (x % 8))) & 0x01;
        canvas.drawPixel(ox + x, oy + y, bit);
      }
    }
    // Give the 1ms scan interrupt a chance to run between panels rather
    // than only before/after this whole ~3000-pixel-op function -- same
    // starvation risk that made the LOADING screen go dark, just less
    // severe here since a full frame update is normally much rarer than
    // the old unthrottled per-loop-iteration redraw was.
    display.loop();
  }
}

// Drains whatever's waiting on Serial, feeding the resync state machine
// described above. Returns true if a full, checksum-valid frame was
// received and applied (at most one render per call). A checksum failure
// isn't reported as a frame -- the caller falls through to the same
// "nothing this tick" handling as if no data had arrived at all.
//
// If rendering ever falls behind the incoming rate, a UDP packet degrades
// gracefully for free (excess packets get dropped by the OS, and whatever
// is read is always a whole intact datagram), but a raw serial byte
// stream has no such buffering -- backlog accumulates until the RX buffer
// overflows, which both corrupts frame alignment and (at this baud) buries
// the CPU in UART interrupts. So this drains a bounded slice per call and
// renders only the newest complete frame found in it; see the latest-wins
// note in the loop body.
static bool serialFrameLoop() {
  // Bounded drain: a continuously-fed stream can otherwise keep
  // Serial.available() non-zero indefinitely and never let this function
  // return, starving the rest of loop(). One RX buffer's worth per call
  // is enough to clear a full backlog in a single pass; anything left
  // over is picked up next call, since serialFrameIdx persists.
  uint16_t budget = 4096;
  bool haveFrame = false;

  while (Serial.available() > 0 && budget-- > 0) {
    // Must happen every iteration -- FthnLabsDisplay's timer ISR only sets
    // a flag; the actual row-scan GPIO work happens here, in
    // display.loop(). Skipping it for a long stretch starves the scan and
    // the panels go dark.
    display.loop();
    uint8_t b = (uint8_t)Serial.read();
    if (serialFrameIdx == 0 && b != FRAME_MAGIC) {
      continue; // still seeking the start of a frame
    }
    serialFrameBuf[serialFrameIdx++] = b;
    if (serialFrameIdx == FRAME_SIZE) {
      serialFrameIdx = 0;
      if (serialFrameBuf[FRAME_SIZE - 1] != frameChecksum(serialFrameBuf)) {
        continue; // misaligned/corrupt window -- discard, resume seeking
      }
      // Latest-wins: stash rather than draw, and keep draining. If more
      // complete frames are already queued behind this one, each
      // overwrites the last, so we render only the freshest and drop the
      // stale ones -- the same thing UDP gets for free from packet
      // semantics. Crucially the render happens unconditionally after the
      // drain, so falling behind costs stale frames, never all of them.
      memcpy(pendingFrame, serialFrameBuf, FRAME_SIZE);
      haveFrame = true;
    }
  }

  if (!haveFrame) {
    return false;
  }

  applyFrame(pendingFrame);
  blitToPanels();
  display.display();
  everLive = true;
  lastFrameMs = millis();
  return true;
}

// Renders a short two-line status message + cycling "still alive" dots
// through the same verified blitToPanels() pipeline as real content.
// Shown while waiting on the first (or next) serial frame from led_bridge.py.
static void showStatus(const char *line1, const char *line2, unsigned long nowMs) {
  canvas.fillScreen(0);
  canvas.setTextSize(2);
  canvas.setTextColor(1);
  canvas.setCursor(8, 6);
  canvas.print(line1);
  canvas.setCursor(14, 26);
  canvas.print(line2);

  // Cycling dots so a long wait never looks frozen.
  uint8_t dots = (nowMs / 400) % 4;
  canvas.setTextSize(1);
  for (uint8_t i = 0; i < dots; i++) {
    canvas.setCursor(4 + i * 8, 40);
    canvas.print(".");
  }

  blitToPanels();
  display.display();
}

void setup() {
  // Default UART RX buffer is only 256 bytes -- smaller than one frame
  // (FRAME_SIZE = 386), so a single render (~19ms, far longer than the
  // ~4ms a frame takes to arrive at this baud) would overflow it and drop
  // bytes mid-frame. 4KB gives roughly ten frames of slack, enough to ride
  // out a render plus any brief stall elsewhere in loop(). Must be set
  // before begin().
  Serial.setRxBufferSize(4096);
  Serial.begin(SERIAL_BAUD);
  delay(1000);
  Serial.println();
  Serial.println("=== BOOT ===");

  Serial.println("[BOOT] display.begin()...");
  if (!display.begin()) {
    Serial.println("Failed to initialize display");
    while (1)
      ;
  }
  Serial.println("[BOOT] display.begin() done.");
  display.setBrightness(BRIGHTNESS);
  showStatus("LOAD", "ING", millis());
}

void loop() {
  // Persistence-of-vision scan -- must run every iteration regardless of
  // link state, or the panels just go dark.
  display.loop();

  // Proof-of-life byte for led_bridge.py -- see HEARTBEAT_BYTE above. Only
  // ever reached once setup() (display.begin() included) is done.
  static unsigned long lastHeartbeatMs = 0;
  unsigned long nowHb = millis();
  if (nowHb - lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
    Serial.write(HEARTBEAT_BYTE);
    lastHeartbeatMs = nowHb;
  }

  bool gotFrame = serialFrameLoop();

  if (gotFrame) {
    return;
  }

  if (everLive && millis() - lastFrameMs <= FRAME_TIMEOUT_MS) {
    // Was live very recently -- ride out a brief hiccup silently rather
    // than flashing back to LOADING for a gap between serial bytes.
    return;
  }

  // Throttled redraw: showStatus() does two full-canvas blit passes
  // (~6000+ pixel ops). Calling it unconditionally on every loop()
  // iteration -- which spins as fast as the CPU allows -- was starving
  // the 1ms scan interrupt of CPU time to actually pulse the LEDs, so the
  // panels stayed dark even though the software was "drawing" correctly.
  // Only re-render a few times a second; loop() spends the rest of its
  // time just calling display.loop(), same as the proven-working
  // QuadrantTest.
  static unsigned long lastStatusRenderMs = 0;
  unsigned long now = millis();
  if (now - lastStatusRenderMs >= 200) {
    showStatus("LOAD", "ING", now);
    lastStatusRenderMs = now;
  }
}
