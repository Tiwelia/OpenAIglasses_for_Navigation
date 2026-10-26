// XIAO ESP32S3 Sense: JPEG-only Wi-Fi preview, no AI or audio.
#include <Arduino.h>
#include <WiFi.h>
#include <esp_camera.h>
#include <ArduinoWebsockets.h>
#if __has_include("secrets.h")
#include "secrets.h"
#else
#include "secrets.example.h"
#endif

using namespace websockets;
// Bound TCP connection/read/write waits without changing the installed library.
class BoundedTcp : public network::Esp32TcpClient {
 public:
  bool connect(const WSString& host, const int port) override {
    client.setTimeout(2000);
    const bool ok = client.connect(host.c_str(), port, 2000);
    client.setNoDelay(true);
    return ok;
  }
  void send(const uint8_t* data, const uint32_t len) override {
    if (client.write(data, len) != len) client.stop();
  }
};
WebsocketsClient socketClient(std::make_shared<BoundedTcp>());
bool cameraReady = false;
bool wasOnline = false;
uint32_t wifiAttempt = 0, socketAttempt = 0, lastFrame = 0;
uint32_t lastPong = 0, lastPing = 0, lastStatus = 0;
uint32_t sentFrames = 0, failures = 0, connections = 0;
constexpr uint32_t FRAME_INTERVAL = 200; // Start conservatively at <=5 fps.

bool initCamera() {
  if (!psramFound()) return false;
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = 15; c.pin_d1 = 17; c.pin_d2 = 18; c.pin_d3 = 16;
  c.pin_d4 = 14; c.pin_d5 = 12; c.pin_d6 = 11; c.pin_d7 = 48;
  c.pin_xclk = 10; c.pin_pclk = 13; c.pin_vsync = 38; c.pin_href = 47;
  c.pin_sccb_sda = 40; c.pin_sccb_scl = 39;
  c.pin_pwdn = -1; c.pin_reset = -1;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_VGA;
  c.jpeg_quality = 12;
  c.fb_count = 1;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  const esp_err_t result = esp_camera_init(&c);
  if (result != ESP_OK) {
    Serial.printf("[ERROR] camera_init=0x%X\n", unsigned(result));
    return false;
  }
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("[WIFI_CAMERA] VGA JPEG; max 5 fps; no AI");
  cameraReady = initCamera();
  if (!cameraReady) Serial.println("[ERROR] camera_or_PSRAM_unavailable");
  socketClient.setUseMasking(true);
  socketClient.onEvent([](WebsocketsEvent event, String) {
    if (event == WebsocketsEvent::GotPong) lastPong = millis();
  });
  WiFi.persistent(false);
  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
    if (event == ARDUINO_EVENT_WIFI_STA_CONNECTED)
      Serial.println("[WIFI] associated_waiting_for_IP");
    else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED)
      Serial.printf("[WIFI] disconnect_reason=%u\n", unsigned(info.wifi_sta_disconnected.reason));
    else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP)
      Serial.println("[WIFI] DHCP_address_received");
  });
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(false);
  wifiAttempt = millis() - 60000;
  socketAttempt = millis() - 3000;
}

void loop() {
  delay(2);
  uint32_t now = millis();
  if (!cameraReady || !strlen(CAMERA_WIFI_SSID)) {
    if (now - lastStatus >= 5000) {
      lastStatus = now;
      Serial.println("[WAIT] check_camera_and_local_secrets_h");
    }
    return;
  }
  if (WiFi.status() != WL_CONNECTED) {
    if (wasOnline) {
      socketClient.close();
      wasOnline = false;
      Serial.println("[WIFI] disconnected");
    }
    if (now - wifiAttempt >= 60000) {
      wifiAttempt = now;
      WiFi.disconnect();
      WiFi.begin(CAMERA_WIFI_SSID, CAMERA_WIFI_PASSWORD);
      Serial.println("[WIFI] connecting (60s retry interval; allow DHCP to finish)");
    }
    return;
  }
  if (!wasOnline) {
    wasOnline = true;
    Serial.printf("[WIFI] connected ip=%s gateway=%s\n",
      WiFi.localIP().toString().c_str(), WiFi.gatewayIP().toString().c_str());
    socketAttempt = now - 3000;
  }
  if (!socketClient.available()) {
    if (now - socketAttempt >= 3000) {
      const String host = strlen(CAMERA_SERVER_HOST) ? String(CAMERA_SERVER_HOST) : WiFi.gatewayIP().toString();
      const bool ok = socketClient.connect(host, CAMERA_SERVER_PORT, "/ws/camera?transport=wifi");
      socketAttempt = millis();
      Serial.printf("[WS] connected=%u\n", ok ? 1U : 0U);
      if (ok) {
        ++connections;
        lastPong = lastPing = millis();
        // Discard frames buffered while offline.
        for (int i = 0; i < 2; ++i) {
          camera_fb_t* stale = esp_camera_fb_get();
          if (stale) esp_camera_fb_return(stale);
        }
      }
    }
    return;
  }
  socketClient.poll();
  now = millis();
  if (now - lastPong > 15000) {
    Serial.println("[WS] heartbeat_timeout");
    socketClient.close();
    return;
  }
  if (now - lastPing >= 5000) {
    lastPing = now;
    socketClient.ping();
  }
  if (socketClient.available() && now - lastFrame >= FRAME_INTERVAL) {
    lastFrame = now;
    camera_fb_t* frame = esp_camera_fb_get();
    bool ok = false;
    if (frame) {
      if (frame->format == PIXFORMAT_JPEG)
        ok = socketClient.sendBinary(reinterpret_cast<const char*>(frame->buf), frame->len) && socketClient.available();
      esp_camera_fb_return(frame);
    }
    if (ok) ++sentFrames;
    else { ++failures; socketClient.close(); }
  }
  if (now - lastStatus >= 5000) {
    lastStatus = now;
    Serial.printf("[STATUS] sent=%lu failures=%lu connections=%lu rssi=%d heap=%lu\n",
      (unsigned long)sentFrames, (unsigned long)failures,
      (unsigned long)connections, WiFi.RSSI(), (unsigned long)ESP.getFreeHeap());
  }
}
