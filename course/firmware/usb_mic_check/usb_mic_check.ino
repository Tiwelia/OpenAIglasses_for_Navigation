// XIAO ESP32S3 Sense: explicit R command records 5 s, then sends PCM over USB.
// No Wi-Fi, camera, AI, gain or automatic recording.
#include <Arduino.h>
#include <ESP_I2S.h>

I2SClass mic;
constexpr size_t PCM_BYTES = 16000 * 2 * 5;
uint8_t* pcm = nullptr;
bool ready = false;

void recordOnce() {
  if (!ready) { Serial.println("[ERROR] not_ready"); return; }
  mic.setPinsPdmRx(42, 41);
  if (!mic.begin(I2S_MODE_PDM_RX, 16000, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    Serial.println("[ERROR] pdm_init"); return;
  }
  char warmup[1024];
  for (int i = 0; i < 8; ++i) mic.readBytes(warmup, sizeof(warmup));
  Serial.println("[RECORDING] seconds=5");
  size_t used = 0;
  const uint32_t start = millis();
  while (used < PCM_BYTES && millis() - start < 8000) {
    const size_t want = min(size_t(1024), PCM_BYTES - used);
    const size_t got = mic.readBytes(reinterpret_cast<char*>(pcm + used), want);
    used += got;
    if (!got) { delay(1); }
  }
  const uint32_t elapsed = millis() - start;
  mic.end();
  if (used != PCM_BYTES) { Serial.printf("[ERROR] short_audio=%u\n", unsigned(used)); return; }
  Serial.printf("[AUDIO] bytes=%u rate=16000 bits=16 channels=1 capture_ms=%lu\n", unsigned(used), (unsigned long)elapsed);
  size_t sent = 0;
  const uint32_t txStart = millis();
  while (sent < used && millis() - txStart < 15000) {
    sent += Serial.write(pcm + sent, min(size_t(1024), used - sent));
    delay(1);
  }
  Serial.println(sent == used ? "\n[AUDIO_END]" : "\n[ERROR] usb_timeout");
}

void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(100);
  pcm = static_cast<uint8_t*>(ps_malloc(PCM_BYTES));
  ready = pcm != nullptr;
}
void loop() {
  if (Serial.available()) {
    const char cmd = Serial.read();
    if (cmd == 'I') Serial.printf("[MIC] ready=%u clock=42 data=41 rate=16000 bits=16 channels=1 seconds=5\n", ready ? 1U : 0U);
    if (cmd == 'R') recordOnce();
  }
  delay(2);
}
