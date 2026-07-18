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

### Botón de la varita

Un botón marca el inicio/fin de cada gesto: se mantiene pulsado mientras se hace el movimiento y se suelta al terminar.

| Botón | ESP32-CAM |
|-------|-----------|
| Pata 1 | GPIO 13 |
| Pata 2 | GND |

Usa el pull-up interno (presionado = a GND). Cámbialo con `PIN_BUTTON` en el `.ino`.
Mientras no tengas el botón soldado, en el estudio puedes usar la **barra espaciadora** como respaldo.

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
2. Comprueba el movimiento en tiempo real:

```powershell
python main.py monitor
```

3. Abre el estudio visual para dibujar, grabar gestos y asignar teclas:

```powershell
python main.py draw
```

En el estudio:
- Mueve la varita para dibujar (el lienzo se limpia solo tras unos segundos quieto).
- **Grabar hechizo** → repite el gesto (por defecto 3 veces) manteniendo el botón de la varita (o la barra espaciadora) → escribe el nombre → pulsa la tecla a asignar.
- **Probar** → haz un gesto y ve qué hechizo detecta y con qué % de confianza (no pulsa teclas). Ideal para afinar umbrales.
- Selecciona un hechizo y **Borrar seleccionado** para eliminarlo.

> Nota: la app reescribe `config/spells.json` al guardar. Si editas los `settings` a mano, hazlo con la app **cerrada**.

4. Lanza hechizos detectando gestos:

```powershell
python main.py cast
```

> `cast` simula teclas. Ejecuta la terminal **como administrador** si Windows bloquea la emulación.
> También existe el CLI `python main.py record --name X --key Y` (grabación simple por movimiento, sin estudio).

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

- Compensación de roll con el acelerómetro (dibujo estable aunque gires la muñeca)
- Calibración de ejes al iniciar
- BLE como alternativa a WiFi
