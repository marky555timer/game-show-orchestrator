#include "boot_status.h"
#include "panel_output.h"

// Kept short enough to fit within the 64px canvas width at text size 1
// (6px/char -- 10-11 chars max) so nothing clips or needs scrolling logic.
static const char *kLine1[] = {
  "BLUETOOTH",  // BLE_WAIT
  "WIFI",       // WIFI_CONNECTING
  "WIFI OK",    // WIFI_CONNECTED
  "",           // LIVE (unused -- caller never renders this state)
  "WIFI LOST",  // RECONNECTING
};

static const char *kLine2[] = {
  "PAIRING...",
  "CONNECTING",
  "WAITING...",
  "",
  "RETRY...",
};

void renderBootStatus(LinkState state, unsigned long nowMs) {
  uint8_t idx = static_cast<uint8_t>(state);

  canvas.fillScreen(0);
  canvas.setTextSize(1);
  canvas.setTextColor(1);

  canvas.setCursor(2, 4);
  canvas.print(kLine1[idx]);
  canvas.setCursor(2, 20);
  canvas.print(kLine2[idx]);

  // Blinking dot, bottom-right corner, ~1Hz: proof of life during a long
  // wait (BLE pairing, a slow WiFi handshake) so the rig never looks like
  // it just froze.
  if ((nowMs / 500) % 2 == 0) {
    canvas.fillRect(PANEL_WIDTH - 4, PANEL_HEIGHT - 4, 3, 3, 1);
  }

  blitToPanels();
  display.display();
}
