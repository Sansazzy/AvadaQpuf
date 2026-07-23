/*
 * AvadaQPuff - VARITA BLE (ESP32-C3 SuperMini) + MPU6050
 *
 * Identidad BLE (debe coincidir con pc_app/ble_receiver.py):
 *   Nombre GAP : "AQ-Wand"
 *   Service    : a0e1d000-1c3b-4a56-8901-abcdef000001
 *   Char NOTIFY: a0e1d001-1c3b-4a56-8901-abcdef000001
 *
 * Payload NOTIFY 14 bytes (little-endian):
 *   int16 ax,ay,az,gx,gy,gz | uint8 btn | uint8 cam
 *
 * Pines C3:
 *   SDA=4  SCL=5  boton hechizos=10  embrague camara=6
 *
 * Arduino: ESP32C3 Dev Module, USB CDC On Boot Enabled, 115200
 */

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <Wire.h>
#include <string.h>

#define DEVICE_NAME  "AQ-Wand"
#define SERVICE_UUID "a0e1d000-1c3b-4a56-8901-abcdef000001"
#define CHAR_UUID    "a0e1d001-1c3b-4a56-8901-abcdef000001"

const int PIN_SDA = 4;
const int PIN_SCL = 5;
const int PIN_BUTTON = 10;   // hechizos (hold)
const int PIN_BUTTON2 = 6;   // embrague camara (toggle)

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
uint32_t notifyCount = 0;
uint32_t lastStatusPrint = 0;
uint32_t advertiseRestarts = 0;

bool camActive = true;
int lastBtn2Reading = HIGH;
int stableBtn2State = HIGH;
uint32_t lastBtn2Change = 0;

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

void updateCamToggle() {
  int reading = digitalRead(PIN_BUTTON2);
  uint32_t now = millis();
  if (reading != lastBtn2Reading) {
    lastBtn2Change = now;
    lastBtn2Reading = reading;
  }
  if ((now - lastBtn2Change) >= DEBOUNCE_MS && reading != stableBtn2State) {
    stableBtn2State = reading;
    if (stableBtn2State == LOW) {
      camActive = !camActive;
      Serial.print("[WAND] cam=");
      Serial.println(camActive ? "ON" : "PAUSA");
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
  Serial.println("=== AvadaQPuff WAND BLE (ESP32-C3) ===");

  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_BUTTON2, INPUT_PULLUP);
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setTimeOut(50);
  Wire.setClock(100000);

  mpuOk = mpuInit();
  if (!mpuOk) Serial.println("[MPU] Continuando SIN MPU (prueba BLE).");

  setupBle();
  Serial.println("[SYS] Listo.");
}

void loop() {
  updateCamToggle();

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
    Serial.print("[SYS] role=WAND ble=");
    Serial.print(deviceConnected ? "ON" : "ADV");
    Serial.print(" mpu=");
    Serial.print(mpuOk ? "OK" : "FAIL");
    Serial.print(" cam=");
    Serial.print(camActive ? "ON" : "OFF");
    Serial.print(" btn=");
    Serial.print(digitalRead(PIN_BUTTON) == LOW ? "1" : "0");
    Serial.print(" n=");
    Serial.print(notifyCount);
    Serial.print(" adv=");
    Serial.println(advertiseRestarts);
  }

  static uint32_t lastSample = 0;
  if (now - lastSample < SAMPLE_INTERVAL_MS) return;
  lastSample = now;
  if (!deviceConnected || pChar == nullptr) return;

  int16_t ax = 0, ay = 0, az = 0, gx = 0, gy = 0, gz = 0;
  if (mpuOk) readMpu(ax, ay, az, gx, gy, gz);

  int16_t vals[6] = {ax, ay, az, gx, gy, gz};
  uint8_t payload[14];
  memcpy(payload, vals, 12);
  payload[12] = (digitalRead(PIN_BUTTON) == LOW) ? 1 : 0;
  payload[13] = camActive ? 1 : 0;

  pChar->setValue(payload, sizeof(payload));
  pChar->notify();
  notifyCount++;
}
