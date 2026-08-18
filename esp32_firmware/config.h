#ifndef CONFIG_H
#define CONFIG_H

#define PANEL_WIDTH 64
#define PANEL_HEIGHT 48

#define BRIGHTNESS 100 // 1 - 100

// One-time seed only -- the real, ongoing source of truth is Preferences
// (NVS flash), set via the in-field setup web page (see wifi_field_setup.*
// / SSID "GameShowDisplay-Setup"). These values just pre-populate storage
// on the very first boot of this firmware so the currently-working venue
// doesn't need to be manually reconfigured; irrelevant after that.
#define WIFI_SSID "baby"
#define WIFI_PASSWORD "babybaby234"

// Pin/panel wiring config (oePin, cfg, etc.) now lives in panel_output.cpp,
// deliberately in the same file as the `display` object that consumes it --
// see the comment there for why.

#endif
