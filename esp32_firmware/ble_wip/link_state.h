#ifndef LINK_STATE_H
#define LINK_STATE_H

// High-level connectivity state, cheapest-to-richest:
//   BLE_WAIT         no WiFi credentials yet -- BLE advertising, waiting to
//                     be paired/provisioned (or re-provisioned).
//   WIFI_CONNECTING  credentials exist, WiFi.begin() in progress.
//   WIFI_CONNECTED   WiFi is up but no show frame has arrived yet.
//   LIVE             actively receiving frames (Display.ino tracks this
//                     itself -- it's not returned by currentLinkState()).
//   RECONNECTING     was LIVE at some point, WiFi has since dropped.
enum class LinkState {
  BLE_WAIT,
  WIFI_CONNECTING,
  WIFI_CONNECTED,
  LIVE,
  RECONNECTING,
};

#endif
