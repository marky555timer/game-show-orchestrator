#include "wifi_field_setup.h"

#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>

#include "config.h"

#define STA_CONNECT_TIMEOUT_MS 15000
#define AP_SSID "GameShowDisplay-Setup"

static Preferences prefs;
static WebServer server(80);
static WifiFieldState state = WifiFieldState::CONNECTING;
static unsigned long connectAttemptStartMs = 0;
static bool serverStarted = false;
static String savedSsid, savedPass;

static const char *kFormPage =
  "<!DOCTYPE html><html><head><title>Game Show Display Setup</title>"
  "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"></head>"
  "<body style=\"font-family:sans-serif;max-width:400px;margin:20px auto;padding:0 16px\">"
  "<h2>Display WiFi Setup</h2>"
  "<form action=\"/save\" method=\"POST\">"
  "<label>Network Name (SSID)</label><br>"
  "<input name=\"ssid\" style=\"width:100%;padding:8px;margin:8px 0\" required><br>"
  "<label>Password</label><br>"
  "<input name=\"pass\" type=\"password\" style=\"width:100%;padding:8px;margin:8px 0\"><br>"
  "<button type=\"submit\" style=\"width:100%;padding:12px;margin-top:12px\">Save &amp; Connect</button>"
  "</form></body></html>";

static void handleRoot() {
  server.send(200, "text/html", kFormPage);
}

static void handleSave() {
  String ssid = server.arg("ssid");
  String pass = server.arg("pass");
  if (ssid.length() == 0) {
    server.send(400, "text/plain", "SSID required.");
    return;
  }

  prefs.begin("wifi", false);
  prefs.putString("ssid", ssid);
  prefs.putString("pass", pass);
  prefs.end();

  server.send(200, "text/html",
    "<html><body style=\"font-family:sans-serif;text-align:center;padding:40px\">"
    "<h2>Saved!</h2><p>Rebooting and connecting to the new network...</p>"
    "</body></html>");
  Serial.printf("[WIFI SETUP] New credentials saved for \"%s\" -- rebooting.\n", ssid.c_str());
  delay(1000); // let the HTTP response actually flush before tearing down
  ESP.restart();
}

static void startConfigServer() {
  if (serverStarted) {
    return;
  }
  server.on("/", HTTP_GET, handleRoot);
  server.on("/wifi", HTTP_GET, handleRoot);
  server.on("/save", HTTP_POST, handleSave);
  server.begin();
  serverStarted = true;
}

static void enterApSetupMode() {
  state = WifiFieldState::AP_SETUP;
  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID);
  startConfigServer();
  Serial.printf("[WIFI SETUP] AP mode: join \"%s\", then visit http://%s/\n",
                AP_SSID, WiFi.softAPIP().toString().c_str());
}

static void beginStaAttempt() {
  Serial.printf("[WIFI] Connecting to \"%s\"...\n", savedSsid.c_str());
  WiFi.mode(WIFI_STA);
  WiFi.begin(savedSsid.c_str(), savedPass.c_str());
  connectAttemptStartMs = millis();
  state = WifiFieldState::CONNECTING;
}

void wifiFieldSetupBegin() {
  prefs.begin("wifi", true);
  savedSsid = prefs.getString("ssid", "");
  savedPass = prefs.getString("pass", "");
  prefs.end();

  if (savedSsid.length() == 0 && sizeof(WIFI_SSID) > 1) {
    // First-ever boot of this firmware -- seed from config.h once so the
    // currently-working venue doesn't need immediate reconfiguration.
    savedSsid = WIFI_SSID;
    savedPass = WIFI_PASSWORD;
    prefs.begin("wifi", false);
    prefs.putString("ssid", savedSsid);
    prefs.putString("pass", savedPass);
    prefs.end();
    Serial.println("[WIFI SETUP] Seeded initial credentials from config.h.");
  }

  if (savedSsid.length() == 0) {
    enterApSetupMode();
    return;
  }

  beginStaAttempt();
}

void wifiFieldSetupLoop() {
  if (state == WifiFieldState::CONNECTING) {
    if (WiFi.status() == WL_CONNECTED) {
      state = WifiFieldState::CONNECTED;
      WiFi.setSleep(false);
      startConfigServer(); // stays reachable while connected, for proactive reconfig
      Serial.print("[WIFI] Connected. IP: ");
      Serial.println(WiFi.localIP());
    } else if (millis() - connectAttemptStartMs > STA_CONNECT_TIMEOUT_MS) {
      Serial.println("[WIFI] Saved credentials failed here -- falling back to setup mode.");
      enterApSetupMode();
    }
    return;
  }

  if (state == WifiFieldState::CONNECTED && WiFi.status() != WL_CONNECTED) {
    Serial.println("[WIFI] Connection lost -- retrying...");
    beginStaAttempt();
    return;
  }

  server.handleClient();
}

WifiFieldState wifiFieldSetupState() {
  return state;
}
