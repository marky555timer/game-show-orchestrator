#include <WiFi.h>
#include <WiFiUdp.h>

#include "config.h"
#include "panel_output.h"
#include "wifi_field_setup.h"

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
// render tick, over either transport below): 1 magic byte + 6 panels x 64
// bytes. Each panel is packed 1bpp, row-major, MSB-first, 4 bytes/row
// (32px wide) x 16 rows -- in chain-letter order A-F, i.e. the same
// logical top-left origins panel_output.cpp's kQuadMap uses as `src`.
//
// Two transports feed the same frame format: WiFi/UDP broadcast (original
// path, works from anywhere on the venue LAN) and USB serial (preferred
// by led_bridge.py when a cable is present -- no WiFi hop, so it dodges
// the RF-jitter stutter the UDP path inherits from the venue network).
// Whichever arrives is applied; there's no arbitration beyond "freshest
// frame wins".
//
// A trailing XOR checksum over the panel payload lets both transports
// reject a corrupted/misaligned frame (treated as "no frame this tick")
// rather than drawing garbage. It matters far more for serial than UDP:
// a UDP datagram is always whole-or-absent, whereas the serial byte
// stream has no framing of its own, so one byte lost to an RX overflow
// shifts the magic-byte resync onto the wrong offset and every window
// after it decodes as noise until something resyncs.
#define UDP_PORT 6767
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
// quiet (WiFi still up, just no packets -- e.g. the orchestrator app isn't
// running) before going back to the LOADING screen.
#define FRAME_TIMEOUT_MS 2000

static const uint16_t kQW = PANEL_WIDTH / 2;  // 32
static const uint16_t kQH = PANEL_HEIGHT / 3; // 16

// Logical top-left origin of each of the 6 panels, in chain-letter order
// A-F -- must match panel_output.cpp's kQuadMap `src` coordinates.
static const uint16_t kPanelSrcX[6] = { 0, kQW, 0, kQW, 0, kQW };
static const uint16_t kPanelSrcY[6] = { 0, 0, kQH, kQH, 2 * kQH, 2 * kQH };

static WiFiUDP udp;
static bool udpStarted = false;
static bool everLive = false;
static unsigned long lastFrameMs = 0;
static uint8_t frameBuf[FRAME_SIZE];

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
// Used for both LOADING (waiting on WiFi/frames) and SETUP mode (WiFi
// field setup fallback active).
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
  // out a render plus any WiFi-stack hiccup. Must be set before begin().
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

  wifiFieldSetupBegin();
}

void loop() {
  // Persistence-of-vision scan -- must run every iteration regardless of
  // link state, or the panels just go dark.
  display.loop();

  wifiFieldSetupLoop();
  WifiFieldState wifiState = wifiFieldSetupState();
  bool wifiUp = (wifiState == WifiFieldState::CONNECTED);

  if (wifiUp && !udpStarted) {
    udp.begin(UDP_PORT);
    udpStarted = true;
  } else if (!wifiUp && udpStarted) {
    udp.stop();
    udpStarted = false;
  }

  // Serial checked first: it's the preferred transport when a USB cable
  // is present, and led_bridge.py only sends one or the other per frame
  // (never both), so there's no real contention in practice.
  bool gotFrame = serialFrameLoop();

  if (!gotFrame && udpStarted) {
    int packetSize = udp.parsePacket();
    if (packetSize == FRAME_SIZE) {
      udp.read(frameBuf, FRAME_SIZE);
      if (frameBuf[0] == FRAME_MAGIC && frameBuf[FRAME_SIZE - 1] == frameChecksum(frameBuf)) {
        applyFrame(frameBuf);
        blitToPanels();
        display.display();
        everLive = true;
        lastFrameMs = millis();
        gotFrame = true;
      }
    } else if (packetSize > 0) {
      udp.flush(); // discard anything malformed/unexpected size
    }
  }

  if (gotFrame) {
    return;
  }

  if (everLive && millis() - lastFrameMs <= FRAME_TIMEOUT_MS) {
    // Was live very recently (over either transport) -- ride out a brief
    // hiccup silently rather than flashing back to LOADING for a dropped
    // packet or a gap between serial bytes.
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
    if (wifiState == WifiFieldState::AP_SETUP) {
      showStatus("SETUP", "MODE", now);
    } else {
      showStatus("LOAD", "ING", now);
    }
    lastStatusRenderMs = now;
  }
}
