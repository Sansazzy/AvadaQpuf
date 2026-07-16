# AvadaQPuff

Varita mágica para emular teclas en PC según patrones de movimiento. Pensado para mapear gestos a hechizos en *Hogwarts Legacy*.

## Hardware

- ESP32-CAM (WiFi; la cámara no se usa en este proyecto)
- MPU6050 (acelerómetro + giroscopio)

### Conexión ESP32-CAM ↔ MPU6050

| MPU6050 | ESP32-CAM |
|---------|-----------|
| VCC     | 3.3V      |
| GND     | GND       |
| SDA     | GPIO 14   |
| SCL     | GPIO 15   |

Si GPIO 15 da problemas al arrancar, prueba **SDA → GPIO 13** y **SCL → GPIO 16**.

> Alimenta el MPU6050 a **3.3V**, no a 5V.

### Flashear el ESP32-CAM

1. Conecta un adaptador USB‑TTL (3.3V) a U0R/U0T.
2. Une **GPIO 0 a GND** al encender para modo programación.
3. Sube el sketch desde `firmware/wand_sender/`.
4. Edita `WIFI_SSID`, `WIFI_PASS` y `PC_IP` en el `.ino`.

## Software PC

Requisitos: Python 3.10+

```powershell
cd pc_app
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### Uso rápido

1. Enciende la varita (ESP32 conectado a WiFi).
2. Visualiza el movimiento en tiempo real:

```powershell
python main.py monitor
```

3. Graba un gesto y asígnale una tecla:

```powershell
python main.py record --name Incendio --key 1
```

4. Lanza hechizos detectando gestos:

```powershell
python main.py cast
```

> `cast` simula teclas. Ejecuta la terminal **como administrador** si Windows bloquea la emulación.

## Estructura

```
AvadaQPuff/
├── firmware/wand_sender/   # Sketch ESP32
├── pc_app/                 # App Python
│   ├── config/spells.json  # Gestos + teclas
│   └── ...
└── README.md
```

## Próximos pasos

- UI gráfica para grabar y asignar teclas
- Calibración de ejes al iniciar
- BLE como alternativa a WiFi
