# FeatherWeather

ESP32-based weather station with GPS timestamping, battery-backed RTC, and SD card logging.

## Hardware

### Feather Stack

| Board | Product | Interface | Purpose |
|-------|---------|-----------|---------|
| [Adafruit ESP32 Feather V2 w.FL](https://www.adafruit.com/product/5438) | #5438 | — | Main MCU, WiFi, BT, NeoPixel |
| [Adalogger FeatherWing](https://www.adafruit.com/product/2922) | #2922 | I2C + SPI | PCF8523 RTC + microSD |
| [Ultimate GPS FeatherWing](https://www.adafruit.com/product/3133) | #3133 | UART | PA1616D GPS/GLONASS |

### Sensors

| Sensor | Product | Interface | I2C Address | Measures |
|--------|---------|-----------|-------------|---------|
| [Adafruit BMP390](https://www.adafruit.com/product/4816) | #4816 | I2C (STEMMA QT) | 0x77 (0x76 alt) | Pressure, altitude, temperature |
| [Adafruit SHTC3](https://www.adafruit.com/product/4636) | #4636 | I2C (STEMMA QT) | 0x70 (fixed) | Temperature, humidity |
| [Seeed HM3301](https://wiki.seeedstudio.com/Grove-Laser_PM2.5_Sensor-HM3301/) | Grove | I2C (Grove) | 0x40 (fixed) | PM1.0, PM2.5, PM10 air quality |
| [DFRobot SEN0575](https://wiki.dfrobot.com/sen0575/) | SEN0575 | I2C | 0x1D (fixed) | Rainfall (mm), tip count, uptime |
| [DFRobot SEN0482](https://wiki.dfrobot.com/sen0482/) | SEN0482 | RS485 Modbus RTU | 0x02 default ⚠ | Wind direction (degrees + 16-pt) |
| [DFRobot SEN0483](https://wiki.dfrobot.com/sen0483/) | SEN0483 | RS485 Modbus RTU | 0x02 default ⚠ | Wind speed (m/s + Beaufort) |
| [DFRobot SEN0644](https://wiki.dfrobot.com/sen0644/) | SEN0644 | RS485 Modbus RTU | 0x01 default | Illuminance (0-200k Lux, IP68) |

### Pin Assignments (ESP32 Feather V2)

| Peripheral | Signal | ESP32 Pin |
|-----------|--------|-----------|
| Adalogger RTC (PCF8523) | SDA | 22 |
| Adalogger RTC (PCF8523) | SCL | 20 |
| Adalogger SD Card | SCK | 5 |
| Adalogger SD Card | MOSI | 19 |
| Adalogger SD Card | MISO | 21 |
| Adalogger SD Card | CS | 33 |
| GPS FeatherWing | TX → RX | 7 |
| GPS FeatherWing | RX → TX | 8 |
| Built-in NeoPixel | Data | 0 |
| BMP390 + SHTC3 | SDA/SCL | STEMMA QT |

### Hardware Notes

- **GPS FeatherWing**: requires UART — works with ESP32 Feather V2. Does not work with Feather 328p, ESP8266, or nRF52832.
- **Adalogger SD**: uses SPI bus; CS is the dedicated SD_CS pin on the wing.
- **Adalogger RTC**: PCF8523 I2C address `0x68`. Requires CR1220 coin cell for battery backup.
- **GPS RTC**: PA1616D has its own built-in RTC; used to sync the PCF8523 on first GPS fix.
- **BMP390**: STEMMA QT I2C, default address `0x77`. Provides pressure (±3 Pa / ±0.25 m), temperature (±0.5 °C).
- **SHTC3**: STEMMA QT I2C, fixed address `0x70`. Provides temperature (±0.2 °C) and humidity (±2 %RH). Chain via QT cable from BMP390 or directly from Feather V2 STEMMA QT port.
- **HM3301**: Grove I2C, fixed address `0x40`. **Must run at ≤ 20 kHz I2C speed.** Allow 30 s warm-up. CRC failures and spurious values are common — the reader retries automatically.
- **RS485 sensors (SEN0482/0483/0644)**: All share one RS485 bus via MAX3485 TTL module. Wired to a dedicated `busio.UART` + one GPIO for DE/~RE direction control. **SEN0482 and SEN0483 both default to Modbus address 0x02** — use `reader.set_address()` with each sensor connected alone to resolve the conflict before deploying on the shared bus.
- **SEN0575**: Set DIP switch to I2C position before use. Fixed address `0x1D`. No official CircuitPython library — uses a ported raw I2C driver. Provides cumulative rainfall (mm), raw tip count, and uptime. Rolling 1-24 hour window available via `read_window(hours)`.
- **I2C address map**: HM3301 `0x40`, PCF8523 `0x68`, SHTC3 `0x70`, BMP390 `0x77`, SEN0575 `0x1D` — no conflicts.
- **Power**: USB-C or LiPoly battery with built-in charging on the Feather V2.

## Setup

### Install dev dependencies

```bash
poetry install
```

### Configure secrets

```bash
cp settings.toml.example settings.toml
# edit settings.toml with your WiFi credentials
```

### Flash CircuitPython firmware

```bash
# Download latest CircuitPython .bin for ESP32 Feather V2 from circuitpython.org
# then flash:
poetry run esptool --chip esp32 --port /dev/ttyUSB0 write_flash -z 0x0 adafruit-circuitpython-adafruit_feather_esp32_v2-en_US-*.bin
```

### Install CircuitPython libraries on device

```bash
# After flashing, install the required libs via circup:
poetry run circup --path /media/$USER/CIRCUITPY install adafruit_gps adafruit_pcf8523 adafruit_sdcard neopixel
```

## Development

### Code Quality

```bash
poetry run lint      # check isort + black + flake8
poetry run format    # auto-fix with isort + black
```

### Testing

```bash
poetry run test      # pytest with coverage → reports/htmlcov/
```

### Tool Reference

| Tool | Version | Purpose |
|------|---------|---------|
| `black` | ^24.10 | Code formatting (88 char) |
| `isort` | ^5.13 | Import sorting |
| `flake8` | ^7.1 | Style checking |
| `pylint` | ^3.3 | Static analysis |
| `pytest` | ^8.3 | Test runner |
| `pytest-cov` | ^6.0 | Coverage → `reports/htmlcov/` |
| `esptool` | ^4.7 | Flash ESP32 firmware |
| `circup` | ^2.0 | Manage CircuitPython libs on device |

## Deployment

### Deploy to device

```bash
# Copy the package to the device library folder
cp -r src/featherweather /media/$USER/CIRCUITPY/lib/

# Copy the main entry point to the device root
cp code.py /media/$USER/CIRCUITPY/code.py

# Copy your configured secrets file
cp settings.toml /media/$USER/CIRCUITPY/settings.toml
```

### Scheduling

`code.py` reads all sensors at fixed 5-minute wall-clock boundaries
(`:00`, `:05`, `:10` … `:55`) using the PCF8523 RTC for alignment.
Between readings it sleeps via `asyncio.sleep()`, which allows a
background GPS task to continuously poll NMEA sentences so that GPS
altitude is always fresh when a sensor cycle runs.

The read interval is controlled by `READ_INTERVAL_MINUTES` in `code.py`.

### RS485 address conflict

SEN0482 (wind direction) and SEN0483 (wind speed) both ship with default
Modbus address `0x02`. Before connecting both to the shared RS485 bus,
reprogram one of them with each sensor connected alone:

```python
# One-time setup: reassign SEN0483 to address 0x03
reader = WindSpeedReader(rs485_uart, de_pin, address=0x02)
reader.set_address(0x03)
# Power-cycle the sensor, then update _WIND_SPD_ADDR in code.py to 0x03
```

## Project Structure

```
code.py                 # device entry point → deploy to CIRCUITPY/
src/featherweather/
├── __init__.py         # package root
├── gps/                # Ultimate GPS FeatherWing [3133] - PA1616D UART
├── rtc/                # Adalogger FeatherWing [2922] - PCF8523 I2C
├── storage/            # Adalogger FeatherWing [2922] - microSD SPI
└── sensors/            # sensor aggregation / coordination
    ├── barometric/     # BMP390 [4816] - pressure + temperature (STEMMA QT I2C)
    ├── temp_humidity/  # SHTC3 [4636] - temperature + humidity (STEMMA QT I2C)
    ├── air_quality/    # HM3301 - PM1.0/PM2.5/PM10 (Grove I2C @ 20 kHz)
    ├── rainfall/       # SEN0575 - tipping bucket rainfall (I2C)
    ├── wind_direction/ # SEN0482 - wind vane 0-360° + 16-pt compass (RS485)
    ├── wind_speed/     # SEN0483 - wind speed m/s + Beaufort (RS485)
    ├── illuminance/    # SEN0644 - 0-200k lux, IP68 waterproof (RS485)
    └── rs485/          # shared Modbus RTU master used by all RS485 sensors
tests/
scripts/
├── lint.py             # isort + black + flake8 runner
└── test.py             # pytest + coverage runner
settings.toml.example   # WiFi / secrets template (copy → settings.toml)
```
