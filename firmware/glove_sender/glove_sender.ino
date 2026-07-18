/*
 * AvadaQPuff - GUANTE de control (ESP32 + MPU6050)
 * Segundo dispositivo, independiente de la varita.
 *
 * Manda la inclinacion (acelerometro) por WiFi (UDP) al PC, que la
 * traduce a WASD. Lleva:
 *   - Boton momentaneo con TOGGLE por software: una pulsacion activa el
 *     envio, otra lo apaga. Asi puedes descansar o recolocar la mano.
 *   - LED indicador: encendido = enviando (toggle activo).
 *
 * Cuando el toggle esta apagado NO se envia nada: el PC detecta el
 * silencio (watchdog) y suelta todas las teclas automaticamente.
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>

// --- Configura tu red y la IP del PC (misma que la varita) ---
const char *WIFI_SSID = "FIBRAZO-312073";
const char *WIFI_PASS = "20149661";
const char *PC_IP = "192.168.1.55";
const uint16_t UDP_PORT = 4210;  // mismo puerto que la varita; se enruta por "id"

// Pines I2C del MPU6050 (ajusta segun tu placa)
const int PIN_SDA = 14;
const int PIN_SCL = 15;

// Boton momentaneo: entre este pin y GND (pull-up interno). Presionado = LOW.
const int PIN_BUTTON = 13;

// LED indicador de "enviando" (toggle activo). Cambia el pin segun tu placa.
// En un ESP32-CAM el LED de flash blanco es el GPIO 4 (muy brillante).
const int PIN_LED = 2;

const uint8_t MPU_ADDR = 0x68;
const uint32_t SAMPLE_INTERVAL_MS = 10;  // ~100 Hz
const uint32_t DEBOUNCE_MS = 50;

WiFiUDP udp;

// Estado del toggle (por software) y del anti-rebote del boton.
bool sending = false;
int lastBtnReading = HIGH;      // HIGH = suelto (pull-up)
int stableBtnState = HIGH;
uint32_t lastBtnChange = 0;

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
  mpuWrite(0x1B, 0x08);  // gyro +-500 deg/s
  mpuWrite(0x1C, 0x08);  // accel +-4 g
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
  // buf[6], buf[7] = temperatura (ignorada)
  gx = (int16_t)((buf[8] << 8) | buf[9]);
  gy = (int16_t)((buf[10] << 8) | buf[11]);
  gz = (int16_t)((buf[12] << 8) | buf[13]);
}

// Lee el boton con anti-rebote y cambia el toggle en el flanco de pulsacion.
void updateToggle() {
  int reading = digitalRead(PIN_BUTTON);
  uint32_t now = millis();

  if (reading != lastBtnReading) {
    lastBtnChange = now;
    lastBtnReading = reading;
  }

  if ((now - lastBtnChange) >= DEBOUNCE_MS && reading != stableBtnState) {
    stableBtnState = reading;
    // Flanco de pulsacion (suelto -> presionado): alterna el envio.
    if (stableBtnState == LOW) {
      sending = !sending;
      digitalWrite(PIN_LED, sending ? HIGH : LOW);
    }
  }
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
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
    Serial.print("IP guante: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("Error WiFi");
  }
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

  connectWiFi();
}

void loop() {
  updateToggle();

  static uint32_t lastSample = 0;
  uint32_t now = millis();
  if (now - lastSample < SAMPLE_INTERVAL_MS) {
    return;
  }
  lastSample = now;

  // Si el toggle esta apagado no enviamos nada (el PC suelta las teclas).
  if (!sending) {
    return;
  }

  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
    return;
  }

  int16_t ax, ay, az, gx, gy, gz;
  readMpu(ax, ay, az, gx, gy, gz);

  float fax = ax / 8192.0f;
  float fay = ay / 8192.0f;
  float faz = az / 8192.0f;
  float fgx = gx / 65.5f;
  float fgy = gy / 65.5f;
  float fgz = gz / 65.5f;

  char payload[192];
  snprintf(payload, sizeof(payload),
           "{\"id\":\"glove\",\"t\":%lu,\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,"
           "\"gx\":%.2f,\"gy\":%.2f,\"gz\":%.2f,\"btn\":0}",
           now, fax, fay, faz, fgx, fgy, fgz);

  udp.beginPacket(PC_IP, UDP_PORT);
  udp.write((const uint8_t *)payload, strlen(payload));
  udp.endPacket();
}
