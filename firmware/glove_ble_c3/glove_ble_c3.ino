/*
 * AvadaQPuff - GUANTE BLE (ESP32-C3 SuperMini) + MPU6050
 *
 * Identidad BLE (debe coincidir con pc_app/ble_receiver.py):
 *   Nombre GAP : "AQ-Glove"
 *   Service    : a0e1d010-1c3b-4a56-8901-abcdef000001   << distinto de la varita
 *   Char NOTIFY: a0e1d011-1c3b-4a56-8901-abcdef000001
 *
 * Payload NOTIFY 14 bytes (mismo formato que la varita):
 *   int16 ax,ay,az,gx,gy,gz | uint8 btn=0 | uint8 cam=0
 *
 * Comportamiento propio del guante:
 *   - Boton toggle: activa/desactiva ENVIO (LED ON = enviando)
 *   - Sin envio -> el PC suelta WASD (watchdog)
 *
 * Pines C3:
 *   SDA=4  SCL=5  boton toggle=10  LED=7
 *
 * Arduino: ESP32C3 Dev Module, USB CDC On Boot Enabled, 115200
 */

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <Wire.h>
#include <string.h>

#define DEVICE_NAME  "AQ-Glove"
#define SERVICE_UUID "a0e1d010-1c3b-4a56-8901-abcdef000001"
#define CHAR_UUID    "a0e1d011-1c3b-4a56-8901-abcdef000001"

const int PIN_SDA = 4;
const int PIN_SCL = 5;
const int PIN_BUTTON = 10;  // toggle de envio
const int PIN_LED = 7;      // indicador enviando

uint8_t mpuAddr = 0x68;
const uint8_t MPU_WHO_AM_I = 0x75;
const uint32_t SAMPLE_INTERVAL_MS = 10;
const uint32_t DEBOUNCE_MS = 50;
const uint32_t STATUS_INTERVAL_MS = 2000;

BLECharacteristic *pChar = nullptr;
BLEServer *pServer = nullptr;
volatile bool deviceConnected = false;
volatile bool justConnected = false;
volatile bool justDisconnected = false;

bool mpuOk = false;
bool sending = false;
uint32_t notifyCount = 0;
uint32_t lastStatusPrint = 0;
uint32_t advertiseRestarts = 0;

int lastBtnReading = HIGH;
int stableBtnState = HIGH;
uint32_t lastBtnChange = 0;

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *s) override {
    deviceConnected = true;
    justConnected = true;
  }
  void onDisconnect(BLEServer *s) override {
    deviceConnected = false;
    justDisconnected = true;
  }
};

void setLed(bool on) {
  digitalWrite(PIN_LED, on ? HIGH : LOW);
}

void mpuWrite(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(mpuAddr);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission(true);
}

uint8_t mpuReadReg(uint8_t reg) {
  Wire.beginTransmission(mpuAddr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return 0xFF;
  Wire.requestFrom(mpuAddr, (uint8_t)1, (uint8_t)true);
  return Wire.available() ? Wire.read() : 0xFF;
}

bool mpuProbeAddr(uint8_t addr) {
  Wire.beginTransmission(addr);
  uint8_t err = Wire.endTransmission();
  Serial.print("[MPU] probe 0x");
  Serial.print(addr, HEX);
  Serial.print(" -> err=");
  Serial.print(err);
  Serial.println(err == 0 ? " (ACK OK)" : " (fail)");
  return err == 0;
}

// Escaneo corto con timeout: el C3 puede colgarse en endTransmission
// si SDA/SCL estan mal / flotando, sin Wire.setTimeOut().
int i2cScan() {
  Serial.println("[I2C] Escaneando (timeout 50 ms/addr)...");
  Wire.setTimeOut(50);
  int found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    if ((addr & 0x0F) == 1) {
      Serial.print("[I2C]   ... 0x");
      if (addr < 16) Serial.print("0");
      Serial.println(addr, HEX);
    }
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.print("[I2C]   encontrado 0x");
      if (addr < 16) Serial.print("0");
      Serial.println(addr, HEX);
      found++;
    }
  }
  if (found == 0) {
    Serial.println("[I2C] (ninguno) VCC->3V3 GND SDA->4 SCL->5 | intercambia SDA/SCL");
  }
  return found;
}

bool mpuInit() {
  Serial.print("[MPU] SDA=GPIO");
  Serial.print(PIN_SDA);
  Serial.print(" SCL=GPIO");
  Serial.println(PIN_SCL);

  Wire.setClock(100000);
  Wire.setTimeOut(50);
  delay(20);

  // Primero solo 0x68/0x69 (rapido). Si fallan, escaneo completo.
  Serial.println("[MPU] Probando 0x68 / 0x69...");
  if (mpuProbeAddr(0x68)) {
    mpuAddr = 0x68;
  } else if (mpuProbeAddr(0x69)) {
    mpuAddr = 0x69;
    Serial.println("[MPU] addr 0x69 (AD0 alto)");
  } else {
    i2cScan();
    Serial.println("[MPU] FAIL: sin ACK en 0x68/0x69");
    return false;
  }

  uint8_t who = mpuReadReg(MPU_WHO_AM_I);
  Serial.print("[MPU] WHO_AM_I=0x");
  Serial.println(who, HEX);
  if (who == 0xFF) return false;

  mpuWrite(0x6B, 0x00);
  delay(50);
  mpuWrite(0x1B, 0x08);
  mpuWrite(0x1C, 0x08);

  Wire.beginTransmission(mpuAddr);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  Wire.requestFrom(mpuAddr, (uint8_t)14, (uint8_t)true);
  if (Wire.available() < 14) return false;
  uint8_t buf[14];
  for (uint8_t i = 0; i < 14; i++) buf[i] = Wire.read();
  int16_t ax = (int16_t)((buf[0] << 8) | buf[1]);
  int16_t ay = (int16_t)((buf[2] << 8) | buf[3]);
  int16_t az = (int16_t)((buf[4] << 8) | buf[5]);
  Serial.print("[MPU] prueba ax=");
  Serial.print(ax);
  Serial.print(" ay=");
  Serial.print(ay);
  Serial.print(" az=");
  Serial.println(az);

  Wire.setClock(400000);
  Serial.print("[MPU] OK addr=0x");
  Serial.println(mpuAddr, HEX);
  return true;
}

void readMpu(int16_t &ax, int16_t &ay, int16_t &az,
             int16_t &gx, int16_t &gy, int16_t &gz) {
  Wire.beginTransmission(mpuAddr);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  Wire.requestFrom(mpuAddr, (uint8_t)14, (uint8_t)true);
  uint8_t buf[14];
  for (uint8_t i = 0; i < 14; i++) buf[i] = Wire.available() ? Wire.read() : 0;
  ax = (int16_t)((buf[0] << 8) | buf[1]);
  ay = (int16_t)((buf[2] << 8) | buf[3]);
  az = (int16_t)((buf[4] << 8) | buf[5]);
  gx = (int16_t)((buf[8] << 8) | buf[9]);
  gy = (int16_t)((buf[10] << 8) | buf[11]);
  gz = (int16_t)((buf[12] << 8) | buf[13]);
}

void updateSendToggle() {
  int reading = digitalRead(PIN_BUTTON);
  uint32_t now = millis();
  if (reading != lastBtnReading) {
    lastBtnChange = now;
    lastBtnReading = reading;
  }
  if ((now - lastBtnChange) >= DEBOUNCE_MS && reading != stableBtnState) {
    stableBtnState = reading;
    if (stableBtnState == LOW) {
      sending = !sending;
      setLed(sending);
      Serial.print("[GLOVE] send=");
      Serial.println(sending ? "ON" : "OFF");
    }
  }
}

void startAdvertising() {
  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(SERVICE_UUID);
  adv->setScanResponse(true);
  adv->setMinPreferred(0x06);
  adv->setMaxPreferred(0x12);
  BLEDevice::startAdvertising();
  advertiseRestarts++;
  Serial.print("[BLE] startAdvertising #");
  Serial.println(advertiseRestarts);
}

void setupBle() {
  Serial.println("[BLE] init...");
  BLEDevice::init(DEVICE_NAME);
  BLEDevice::setPower(ESP_PWR_LVL_P9);
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  BLEService *service = pServer->createService(SERVICE_UUID);
  pChar = service->createCharacteristic(
      CHAR_UUID,
      BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  pChar->addDescriptor(new BLE2902());
  service->start();
  startAdvertising();

  Serial.print("[BLE] name=");
  Serial.println(DEVICE_NAME);
  Serial.print("[BLE] service=");
  Serial.println(SERVICE_UUID);
  Serial.print("[BLE] char=");
  Serial.println(CHAR_UUID);
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println("=== AvadaQPuff GLOVE BLE (ESP32-C3) ===");

  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
  setLed(false);
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setTimeOut(50);
  Wire.setClock(100000);

  mpuOk = mpuInit();
  if (!mpuOk) Serial.println("[MPU] Continuando SIN MPU (prueba BLE).");

  setupBle();
  Serial.println("[SYS] Listo. Pulsa boton para send=ON (LED).");
}

void loop() {
  updateSendToggle();

  if (justConnected) {
    justConnected = false;
    notifyCount = 0;
    Serial.println("[BLE] *** CONECTADO ***");
  }
  if (justDisconnected) {
    justDisconnected = false;
    Serial.print("[BLE] *** DESCONECTADO *** notifies=");
    Serial.println(notifyCount);
    delay(200);
    startAdvertising();
  }

  uint32_t now = millis();
  if (now - lastStatusPrint >= STATUS_INTERVAL_MS) {
    lastStatusPrint = now;
    Serial.print("[SYS] role=GLOVE ble=");
    Serial.print(deviceConnected ? "ON" : "ADV");
    Serial.print(" mpu=");
    Serial.print(mpuOk ? "OK" : "FAIL");
    Serial.print(" send=");
    Serial.print(sending ? "ON" : "OFF");
    Serial.print(" n=");
    Serial.print(notifyCount);
    Serial.print(" adv=");
    Serial.println(advertiseRestarts);
  }

  static uint32_t lastSample = 0;
  if (now - lastSample < SAMPLE_INTERVAL_MS) return;
  lastSample = now;

  // Solo notifica con PC conectado Y toggle ON.
  if (!deviceConnected || !sending || pChar == nullptr) return;

  int16_t ax = 0, ay = 0, az = 0, gx = 0, gy = 0, gz = 0;
  if (mpuOk) readMpu(ax, ay, az, gx, gy, gz);

  int16_t vals[6] = {ax, ay, az, gx, gy, gz};
  uint8_t payload[14];
  memcpy(payload, vals, 12);
  payload[12] = 0;
  payload[13] = 0;

  pChar->setValue(payload, sizeof(payload));
  pChar->notify();
  notifyCount++;
}
