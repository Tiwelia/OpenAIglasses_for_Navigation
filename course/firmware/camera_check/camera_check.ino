// XIAO ESP32S3 Sense: capture one JPEG per USB command, no Wi-Fi required.
// Commands: I = status; C = fresh still (discard 2); V = next preview frame.
// Protocol: ASCII "[FRAME] bytes=N width=W height=H capture_ms=T\n",
// then exactly N JPEG bytes, then "\n[FRAME_END]\n".
#include <Arduino.h>
#include <esp_camera.h>

bool cameraReady = false;
uint32_t captureCount = 0;

void printStatus() {
  sensor_t *sensor = cameraReady ? esp_camera_sensor_get() : nullptr;
  Serial.printf("[CAM] ready=%u pid=0x%04X flash=%lu psram=%lu free_psram=%lu captures=%lu\n",
                cameraReady ? 1U : 0U,
                sensor ? static_cast<unsigned>(sensor->id.PID) : 0U,
                static_cast<unsigned long>(ESP.getFlashChipSize()),
                static_cast<unsigned long>(ESP.getPsramSize()),
                static_cast<unsigned long>(ESP.getFreePsram()),
                static_cast<unsigned long>(captureCount));
}

void captureOne(bool freshStill) {
  if (!cameraReady) {
    Serial.println("[CAM_ERROR] camera_not_ready");
    return;
  }
  // Discard stale frames before the requested capture.
  for (int i = 0; i < (freshStill ? 2 : 0); ++i) {
    camera_fb_t *stale = esp_camera_fb_get();
    if (stale) esp_camera_fb_return(stale);
  }
  const unsigned long start = millis();
  camera_fb_t *frame = esp_camera_fb_get();
  const unsigned long elapsed = millis() - start;
  if (!frame) {
    Serial.println("[CAM_ERROR] capture_failed");
    return;
  }
  if (frame->format != PIXFORMAT_JPEG) {
    esp_camera_fb_return(frame);
    Serial.println("[CAM_ERROR] not_jpeg");
    return;
  }
  Serial.printf("[FRAME] bytes=%u width=%u height=%u capture_ms=%lu\n",
                static_cast<unsigned>(frame->len), frame->width, frame->height, elapsed);
  size_t written = 0;
  const unsigned long transferStart = millis();
  while (written < frame->len && millis() - transferStart < 15000) {
    const size_t sent = Serial.write(frame->buf + written, frame->len - written);
    written += sent;
    if (sent == 0) delay(1);
  }
  const bool ok = written == frame->len;
  esp_camera_fb_return(frame);
  if (ok) {
    ++captureCount;
    Serial.println("\n[FRAME_END]");
  } else {
    Serial.println("\n[CAM_ERROR] usb_write_timeout");
  }
}

void setup() {
  Serial.begin(115200);
  const unsigned long waitStart = millis();
  while (!Serial && millis() - waitStart < 3000) delay(10);
  Serial.println("\n[CAM] XIAO ESP32S3 Sense USB snapshot check");
  if (!psramFound()) {
    Serial.println("[CAM_ERROR] PSRAM_missing_check_OPI_setting");
    return;
  }
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = 15;
  config.pin_d1 = 17;
  config.pin_d2 = 18;
  config.pin_d3 = 16;
  config.pin_d4 = 14;
  config.pin_d5 = 12;
  config.pin_d6 = 11;
  config.pin_d7 = 48;
  config.pin_xclk = 10;
  config.pin_pclk = 13;
  config.pin_vsync = 38;
  config.pin_href = 47;
  config.pin_sccb_sda = 40;
  config.pin_sccb_scl = 39;
  config.pin_pwdn = -1;
  config.pin_reset = -1;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;
  config.jpeg_quality = 12;
  config.fb_count = 1;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  const esp_err_t result = esp_camera_init(&config);
  if (result != ESP_OK) {
    Serial.printf("[CAM_ERROR] init_failed=0x%X\n", static_cast<unsigned>(result));
    return;
  }
  cameraReady = true;
  // Let the sensor's default automatic exposure settle.
  delay(800);
  printStatus();
  Serial.println("[CAM] Commands: I=status, C=fresh still, V=preview frame");
}

void loop() {
  if (Serial.available()) {
    const char command = static_cast<char>(Serial.read());
    if (command == 'I') printStatus();
    else if (command == 'C') captureOne(true);
    else if (command == 'V') captureOne(false);
  }
  delay(5);
}
