#ifndef WIFI_PROVISIONING_H
#define WIFI_PROVISIONING_H

#include "link_state.h"

// Starts BLE advertising under a unique per-device name ("GSO-Display-XXXX")
// with a bonded/encrypted "set WiFi credentials" characteristic -- the show
// PC pairs to this once, like pairing a Bluetooth mouse, and that bond
// persists in flash from then on (no re-pairing after a reboot). If
// credentials were saved from a previous session, immediately attempts to
// reconnect with them too, so a power blip recovers on its own without
// waiting on the PC to re-provision. Call once from setup().
void wifiProvisioningBegin();

// Current high-level link state, derived from whether credentials exist yet
// and the live WiFi.status(). Knows nothing about UDP/frame liveness --
// LIVE and RECONNECTING are layered on top by Display.ino.
LinkState currentLinkState();

#endif
