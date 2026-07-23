/*
 * AvadaQPuff - VARITA en ESP32-C3 SuperMini + MPU6050
 * Misma lógica que wand_sender.ino (ESP32-CAM), solo cambian los pines.
 * Envía lecturas del IMU por WiFi (UDP) al PC.
 *
 * En Arduino IDE:
 *   - Placa: "ESP32C3 Dev Module" (o el perfil del SuperMini).
 *   - "USB CDC On Boot: Enabled" para ver el monitor serie por USB.
 *
 * Pines del C3 SuperMini (evitando strapping: GPIO2, GPIO8, GPIO9, y UART 20/21):
 *   MPU SDA -> GPIO4     MPU SCL -> GPIO5
 *   Botón hechizos -> GPIO10 (a GND)
 *   Botón 2 embrague cámara -> GPIO6 (a GND)
 *   VCC MPU -> 3V3   GND -> GND
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>

// --- Configura tu red y la IP del PC ---
const char *WIFI_SSID = "FIBRAZO-312073";
const char *WIFI_PASS = "20149661";
const char *PC_IP = "192.168.1.56";  // IP de tu PC en la misma red
const uint16_t UDP_PORT = 4210;

// Pines I2C del C3 SuperMini
const int PIN_SDA = 4;
const int PIN_SCL = 5;

// Boton de la varita (hechizos): entre este pin y GND (pull-up interno).
// Presionado = LOW.
const int PIN_BUTTON = 10;

// Boton 2 (EMBRAGUE de camara): momentaneo entre este pin y GND. Toggle por
// software: activa/desactiva el control de camara para pausar, recolocar la
// muñeca y seguir girando. No afecta a los hechizos.
const int PIN_BUTTON2 = 6;

const uint8_t MPU_ADDR = 0x68;
const uint32_t SAMPLE_INTERVAL_MS = 10;  // ~100 Hz
const uint32_t DEBOUNCE_MS = 50;

WiFiUDP udp;

// Estado del embrague de camara (toggle) y su anti-rebote.
bool camActive = true;  // arranca con la camara activa
int lastBtn2Reading = HIGH;
int stableBtn2State = HIGH;
uint32_t lastBtn2Change = 0;

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
  mpuWrite(0x6B, 0x00);  // despertar
  mpuWrite(0x1B, 0x08);  // gyro ±500 °/s
  mpuWrite(0x1C, 0x08);  // accel ±4 g
  return true;
}

void readMpu(int16_t &ax, int16_t &ay, int16_t &az,
             int16_t &gx, int16_t &gy, int16_t &gz) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, (uint8_t)14, (uint8_t)true);

  // Leer a un buffer: el orden de evaluacion de Wire.read() dentro de una
  // misma expresion no esta garantizado en C++.
  uint8_t buf[14];
  for (uint8_t i = 0; i < 14; i++) {
    buf[i] = Wire.read();
  }

  ax = (int16_t)((buf[0] << 8) | buf[1]);
  ay = (int16_t)((buf[2] << 8) | buf[3]);
  az = (int16_t)((buf[4] << 8) | buf[5]);
  // buf[6], buf[7] = temperatura (ignorada)
  gx = (int16_t)((buf[8] << 8) | buf[9]);
  gy = (int16_t)((buf[10] << 8) | buf[11]);
  gz = (int16_t)((buf[12] << 8) | buf[13]);
}

// Lee el boton 2 con anti-rebote y alterna el embrague en el flanco de pulsacion.
void updateCamToggle() {
  int reading = digitalRead(PIN_BUTTON2);
  uint32_t now = millis();

  if (reading != lastBtn2Reading) {
    lastBtn2Change = now;
    lastBtn2Reading = reading;
  }

  if ((now - lastBtn2Change) >= DEBOUNCE_MS && reading != stableBtn2State) {
    stableBtn2State = reading;
    if (stableBtn2State == LOW) {  // suelto -> presionado
      camActive = !camActive;
    }
  }
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  // FIX conocido del ESP32-C3 SuperMini: con la potencia TX por defecto muchas
  // de estas placas NO conectan al WiFi. Bajarla a 8.5 dBm lo soluciona.
  WiFi.setTxPower(WIFI_POWER_8_5dBm);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Conectando WiFi");
  uint8_t attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 60) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("IP varita: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("Error WiFi");
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_BUTTON2, INPUT_PULLUP);
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(400000);

  if (!mpuInit()) {
    Serial.println("MPU6050 no detectado. Revisa cableado I2C.");
    while (true) {
      delay(1000);
    }
  }
  Serial.println("MPU6050 OK");

  connectWiFi();
}

void loop() {
  // Polling del embrague en cada iteracion (mas rapido que el ritmo de muestreo).
  updateCamToggle();

  static uint32_t lastSample = 0;
  uint32_t now = millis();
  if (now - lastSample < SAMPLE_INTERVAL_MS) {
    return;
  }
  lastSample = now;

  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
    return;
  }

  int16_t ax, ay, az, gx, gy, gz;
  readMpu(ax, ay, az, gx, gy, gz);

  // Valores en unidades físicas aproximadas
  float fax = ax / 8192.0f;
  float fay = ay / 8192.0f;
  float faz = az / 8192.0f;
  float fgx = gx / 65.5f;
  float fgy = gy / 65.5f;
  float fgz = gz / 65.5f;

  // Boton: presionado (a GND) = 1
  int btn = (digitalRead(PIN_BUTTON) == LOW) ? 1 : 0;
  int cam = camActive ? 1 : 0;

  char payload[208];
  snprintf(payload, sizeof(payload),
           "{\"id\":\"wand\",\"t\":%lu,\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,"
           "\"gx\":%.2f,\"gy\":%.2f,\"gz\":%.2f,\"btn\":%d,\"cam\":%d}",
           now, fax, fay, faz, fgx, fgy, fgz, btn, cam);

  udp.beginPacket(PC_IP, UDP_PORT);
  udp.write((const uint8_t *)payload, strlen(payload));
  udp.endPacket();
}
