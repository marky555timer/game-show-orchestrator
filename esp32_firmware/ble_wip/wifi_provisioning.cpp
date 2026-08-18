#include "wifi_provisioning.h"

#include <WiFi.h>
#include <Preferences.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLESecurity.h>
#include <esp_mac.h>

// Fixed, arbitrary 128-bit UUIDs for this rig's provisioning service --
// don't need to be "registered" anywhere, just need to match between this
// firmware and the orchestrator's BLE client.
#define SERVICE_UUID   "6c9c0001-2f3a-4d8e-9d2a-0c9f2a6b1a01"
#define CHAR_WIFI_UUID "6c9c0002-2f3a-4d8e-9d2a-0c9f2a6b1a01"

static Preferences prefs;
static bool haveCredentials = false;

static void connectWifi(const String &ssid, const String &pass) {
  Serial.printf("[WIFI] Connecting to \"%s\"...\n", ssid.c_str());
  WiFi.disconnect();
  WiFi.begin(ssid.c_str(), pass.c_str());
}

static void saveCredentials(const String &ssid, const String &pass) {
  prefs.begin("wifi", false);
  prefs.putString("ssid", ssid);
  prefs.putString("pass", pass);
  prefs.end();
}

// Handles the BLE write that delivers WiFi credentials. Only reachable at
// all over a bonded/encrypted connection -- see the ESP_GATT_PERM_WRITE_
// ENCRYPTED permission set on the characteristic below -- so an
// unpaired/unknown BLE device can't push credentials to this rig.
class WifiCharCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *pChar) override {
    // Payload format: "SSID\nPASSWORD" (plain text -- the BLE link itself
    // is what's encrypted, by virtue of the bonded connection this
    // characteristic requires).
    std::string value = pChar->getValue();
    size_t sep = value.find('\n');
    if (sep == std::string::npos) {
      Serial.println("[BLE] Malformed WiFi credential write, ignoring.");
      return;
    }
    String ssid = String(value.substr(0, sep).c_str());
    String pass = String(value.substr(sep + 1).c_str());
    Serial.printf("[BLE] Received WiFi credentials for \"%s\".\n", ssid.c_str());
    saveCredentials(ssid, pass);
    haveCredentials = true;
    connectWifi(ssid, pass);
  }
};

// "GSO-Display-XXXX", suffixed with the last two MAC bytes so multiple
// rigs (if this setup is ever duplicated) show up as distinguishable BLE
// devices during first-time pairing rather than all sharing one name.
static String buildDeviceName() {
  // Reads the factory-burned MAC straight from eFuse -- deliberately not
  // WiFi.macAddress(), which requires the WiFi driver to already be
  // touched. BLE gets initialized before WiFi ever is (see
  // wifiProvisioningBegin()'s ordering note), so nothing here can depend
  // on WiFi being up yet.
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  char suffix[5];
  snprintf(suffix, sizeof(suffix), "%02X%02X", mac[4], mac[5]);
  return String("GSO-Display-") + suffix;
}

void wifiProvisioningBegin() {
  String deviceName = buildDeviceName();
  Serial.printf("[BLE] Device name: \"%s\"\n", deviceName.c_str());

  Serial.println("[BLE] BLEDevice::init()...");
  BLEDevice::init(deviceName.c_str());
  Serial.println("[BLE] BLEDevice::init() done.");

  // Just-Works bonding: no PIN/passkey prompt (the two devices are
  // physically together for the one-time pairing step), but the bond is
  // saved in flash afterward so every later boot reconnects without
  // re-pairing -- same trust model as pairing a Bluetooth mouse once.
  // Classic Bluedroid BLESecurity (esp32 core 2.0.x) -- instance methods,
  // unlike the newer NimBLE-compat shim's static ones.
  Serial.println("[BLE] Configuring security...");
  BLESecurity *pSecurity = new BLESecurity();
  pSecurity->setAuthenticationMode(ESP_LE_AUTH_REQ_SC_BOND);
  pSecurity->setCapability(ESP_IO_CAP_NONE);
  pSecurity->setInitEncryptionKey(ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK);
  pSecurity->setRespEncryptionKey(ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK);
  Serial.println("[BLE] Security configured.");

  Serial.println("[BLE] Creating server/service/characteristic...");
  BLEServer *pServer = BLEDevice::createServer();
  BLEService *pService = pServer->createService(SERVICE_UUID);

  BLECharacteristic *pWifiChar = pService->createCharacteristic(
      CHAR_WIFI_UUID, BLECharacteristic::PROPERTY_WRITE);
  // Requires a bonded/encrypted link -- an unpaired device's write is
  // rejected at the BLE stack level, before WifiCharCallbacks ever runs.
  pWifiChar->setAccessPermissions(ESP_GATT_PERM_WRITE_ENCRYPTED);
  pWifiChar->setCallbacks(new WifiCharCallbacks());

  pService->start();
  Serial.println("[BLE] Service started.");

  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  Serial.println("[BLE] Starting advertising...");
  BLEDevice::startAdvertising();
  Serial.println("[BLE] Advertising started -- pair the orchestrator PC to this name once.");

  // Try last-known credentials immediately (survives a power-cycle mid
  // show without needing the PC to re-provision), while BLE stays up in
  // parallel in case they've changed (new venue, new password).
  Serial.println("[BLE] Checking for saved WiFi credentials...");
  prefs.begin("wifi", true);
  String savedSsid = prefs.getString("ssid", "");
  String savedPass = prefs.getString("pass", "");
  prefs.end();
  Serial.println("[BLE] Preferences checked.");

  if (savedSsid.length() > 0) {
    haveCredentials = true;
    connectWifi(savedSsid, savedPass);
  }
}

LinkState currentLinkState() {
  if (!haveCredentials) {
    return LinkState::BLE_WAIT;
  }
  return (WiFi.status() == WL_CONNECTED) ? LinkState::WIFI_CONNECTED
                                          : LinkState::WIFI_CONNECTING;
}
