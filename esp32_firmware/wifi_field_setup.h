#ifndef WIFI_FIELD_SETUP_H
#define WIFI_FIELD_SETUP_H

// Field-changeable WiFi: no BLE, no laptop required. Saved credentials
// live in Preferences (NVS flash). If they fail to connect (new venue,
// wrong network) within a timeout, the rig automatically falls back to
// hosting its own temporary WiFi network ("GameShowDisplay-Setup") with a
// one-page config form -- connect any phone, fill in the new network's
// name/password, submit, done. The same form stays reachable while
// normally connected too, for switching venues proactively.
enum class WifiFieldState {
  CONNECTING, // attempting saved credentials
  CONNECTED,  // STA connected, normal operation
  AP_SETUP,   // fallback: hosting the setup network + config page
};

// Call once from setup(). Loads (and, on first-ever boot, seeds from
// config.h's WIFI_SSID/WIFI_PASSWORD) saved credentials, then kicks off
// the initial connection attempt -- or goes straight to AP_SETUP if
// nothing is saved.
void wifiFieldSetupBegin();

// Call every loop() iteration. Non-blocking: advances the connection
// timeout, retries on a dropped connection, and services the config web
// server. Submitting new credentials through the web form reboots the
// device on its own.
void wifiFieldSetupLoop();

WifiFieldState wifiFieldSetupState();

#endif
