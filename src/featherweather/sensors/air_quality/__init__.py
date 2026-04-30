"""Air quality / particulate matter sensor (HM3301).

Hardware: Seeed Studio Grove Laser PM2.5 Sensor (HM3301)
          https://wiki.seeedstudio.com/Grove-Laser_PM2.5_Sensor-HM3301/
Chip: Honeywell HM3301
Interface: I2C (Grove), fixed address 0x40

IMPORTANT: Set I2C bus speed to <= 20 kHz for reliable readings:
    busio.I2C(board.SCL, board.SDA, frequency=20_000)

Allow 30 seconds warm-up after power-on. CRC failures and out-of-range
values are common — the reader retries automatically.

Ported from https://github.com/tim-oe/WeatherWatch
(weatherwatch/sensor/aqi/)
"""
