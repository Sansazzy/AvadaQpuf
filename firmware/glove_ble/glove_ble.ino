/*
 * AvadaQPuff - GUANTE por BLUETOOTH (BLE) + MPU6050
 * Segundo dispositivo, independiente de la varita. Sin WiFi/IP.
 * Se anuncia como "AvadaQPuff-Glove"; el PC lo detecta y conecta.
 *
 * Toggle por software (boton momentaneo): activa/desactiva el ENVIO. Con el LED
 * como indicador. Cuando esta apagado NO notifica: el PC (watchdog) suelta las
 * teclas WASD solo. Asi puedes descansar o recolocar la mano.
 *
 * Paquete NOTIFY de 14 bytes (igual formato que la varita):
 *   int16 ax, ay, az, gx, gy, gz  (crudos, little-endian)
 *   uint8 btn=0, uint8 cam=0      (no se usan en el guante)
 *
 * Pines por defecto para ESP32-CAM (cambia si usas un C3):
 *   SDA=14, SCL=15, boton toggle=13, LED=2
 */

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <Wire.h>
#include <string.h>

#define DEVICE_NAME "AQ-Glove"
#define SERVICE_UUID "a0e1d010-1c3b-4a56-8901-abcdef000001"
#define CHAR_UUID    "a0e1d011-1c3b-4a56-8901-abcdef000001"

// --- Pines (ESP32-CAM). En un C3 usa p.ej. SDA=4, SCL=5, boton=10, LED=8 ---
const int PIN_SDA = 14;
const int PIN_SCL = 15;
const int PIN_BUTTON = 13;   // toggle de envio (a GND)
const int PIN_LED = 2;       // indicador: encendido = enviando

const uint8_t MPU_ADDR = 0x68;
const uint32_t SAMPLE_INTERVAL_MS = 10;  // ~100 Hz
const uint32_t DEBOUNCE_MS = 50;

BLECharacteristic *pChar = nullptr;
bool deviceConnected = false;

// Toggle de envio (con anti-rebote).
bool sending = false;
int lastBtnReading = HIGH;
int stableBtnState = HIGH;
uint32_t lastBtnChange = 0;

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *s) override { deviceConnected = true; }
  void onDisconnect(BLEServer *s) override {
    deviceConnected = false;
    BLEDevice::startAdvertising();
  }
};

void mpuWrite(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission(true);
}

bool mpuInit() {
  Wire.beginTransmission(MPU_ADDR);
  if (Wire.endTransmission() != 0) {
    return false;
  }
  mpuWrite(0x6B, 0x00);
  mpuWrite(0x1B, 0x08);
  mpuWrite(0x1C, 0x08);
  return true;
}

void readMpu(int16_t &ax, int16_t &ay, int16_t &az,
             int16_t &gx, int16_t &gy, int16_t &gz) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, (uint8_t)14, (uint8_t)true);

  uint8_t buf[14];
  for (uint8_t i = 0; i < 14; i++) {
    buf[i] = Wire.read();
  }

  ax = (int16_t)((buf[0] << 8) | buf[1]);
  ay = (int16_t)((buf[2] << 8) | buf[3]);
  az = (int16_t)((buf[4] << 8) | buf[5]);
  gx = (int16_t)((buf[8] << 8) | buf[9]);
  gy = (int16_t)((buf[10] << 8) | buf[11]);
  gz = (int16_t)((buf[12] << 8) | buf[13]);
}

void updateToggle() {
  int reading = digitalRead(PIN_BUTTON);
  uint32_t now = millis();
  if (reading != lastBtnReading) {
    lastBtnChange = now;
    lastBtnReading = reading;
  }
  if ((now - lastBtnChange) >= DEBOUNCE_MS && reading != stableBtnState) {
    stableBtnState = reading;
    if (stableBtnState == LOW) {  // suelto -> presionado
      sending = !sending;
      digitalWrite(PIN_LED, sending ? HIGH : LOW);
    }
  }
}

void setupBle() {
  BLEDevice::init(DEVICE_NAME);
  BLEServer *server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  BLEService *service = server->createService(SERVICE_UUID);
  pChar = service->createCharacteristic(
      CHAR_UUID,
      BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  pChar->addDescriptor(new BLE2902());
  service->start();

  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(SERVICE_UUID);
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(400000);

  if (!mpuInit()) {
    Serial.println("MPU6050 no detectado. Revisa cableado I2C.");
    while (true) {
      delay(1000);
    }
  }
  Serial.println("MPU6050 OK (guante)");

  setupBle();
  Serial.println("BLE anunciando como " DEVICE_NAME);
}

void loop() {
  updateToggle();

  static uint32_t lastSample = 0;
  uint32_t now = millis();
  if (now - lastSample < SAMPLE_INTERVAL_MS) {
    return;
  }
  lastSample = now;

  // Solo notifica si hay conexion y el toggle esta activo.
  if (!deviceConnected || !sending) {
    return;
  }

  int16_t ax, ay, az, gx, gy, gz;
  readMpu(ax, ay, az, gx, gy, gz);

  int16_t vals[6] = {ax, ay, az, gx, gy, gz};
  uint8_t payload[14];
  memcpy(payload, vals, 12);
  payload[12] = 0;  // btn (sin uso en guante)
  payload[13] = 0;  // cam (sin uso en guante)

  pChar->setValue(payload, sizeof(payload));
  pChar->notify();
}
