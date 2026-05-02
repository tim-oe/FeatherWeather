"""FeatherWeather – main entry point for ESP32 Feather V2.

Deploy this file to the root of the CircuitPython device (CIRCUITPY/code.py).
The featherweather package must be copied to CIRCUITPY/lib/featherweather/.

Scheduling strategy:
    Sensor reads happen at fixed N-minute boundaries (cron-style) aligned to
    wall-clock time from the PCF8523 RTC (e.g. :00, :05, :10 ... :55).
    Between readings the main task sleeps via asyncio.sleep(), allowing the
    GPS polling task to keep processing NMEA sentences in the background.

Parallelism note:
    CircuitPython asyncio is cooperative, not preemptive. Blocking I2C / UART
    calls will not overlap. I2C sensor reads finish in ~5-20 ms each, and
    RS485 Modbus round-trips take ~50-200 ms each. Total read cycle is well
    under one second, so sequential reads at a 5-minute interval is fine.
    asyncio.gather() is used for structural clarity and future async drivers.
    RS485 sensors MUST remain sequential — they share one UART bus.

Pin assignments (adjust to match your wiring):
    I2C  SCL / SDA        board.SCL / board.SDA   (STEMMA QT / FeatherWing)
    SPI  SCK / MOSI / MISO board.SCK / MOSI / MISO (SD card on Adalogger)
    SPI  CS               board.D10               (SD chip-select)
    UART1 TX / RX         board.TX / board.RX     (GPS FeatherWing, 9600 baud)
    UART2 TX / RX         board.A0 / board.A1     (RS485 MAX3485, 9600 baud)
    DE   MAX3485 DE/~RE   board.D5                (RS485 direction control)

Modbus addresses (reprogram conflicting sensors before first use):
    SEN0482 Wind Direction  0x02  (default; run set_address() if conflict)
    SEN0483 Wind Speed      0x03  (default is 0x02 — must reassign to 0x03)
    SEN0644 Illuminance     0x01  (default)
"""

import asyncio
import os

import adafruit_pcf8523
import board
import busio
import digitalio

from featherweather.sensors.air_quality.air_quality_reader import AirQualityReader
from featherweather.sensors.barometric.barometric_reader import BarometricReader
from featherweather.gps.gps_reader import GpsReader
from featherweather.sensors.illuminance.illuminance_reader import IlluminanceReader
from featherweather.sensors.rainfall.rainfall_reader import RainfallReader
from featherweather.sensors.temp_humidity.temp_humidity_reader import TempHumidityReader
from featherweather.sensors.wind_direction.wind_direction_reader import WindDirectionReader
from featherweather.sensors.wind_speed.wind_speed_reader import WindSpeedReader

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

READ_INTERVAL_MINUTES: int = int(os.getenv("READ_INTERVAL_MINUTES") or 5)

_GPS_UPDATE_INTERVAL_S: float = 0.2   # poll GPS UART at 5 Hz
_GPS_BAUD: int = int(os.getenv("GPS_BAUD") or 9600)

_RS485_BAUD: int = int(os.getenv("RS485_BAUD") or 9600)
_RS485_TIMEOUT_S: float = int(os.getenv("RS485_TIMEOUT_MS") or 500) / 1000

# Modbus slave addresses — change if you have reassigned them
_WIND_DIR_ADDR: int = 0x02
_WIND_SPD_ADDR: int = 0x03  # reprogrammed from default 0x02 to avoid conflict
_ILLUMINANCE_ADDR: int = 0x01

# ---------------------------------------------------------------------------
# Shared GPS state — written by gps_task, read by sensor_cycle_task
# ---------------------------------------------------------------------------

_gps_altitude_m: float | None = None


# ---------------------------------------------------------------------------
# Scheduling helpers
# ---------------------------------------------------------------------------


def _seconds_until_next_interval(rtc: adafruit_pcf8523.PCF8523) -> float:
    """Return seconds until the next READ_INTERVAL_MINUTES boundary.

    Examples (5-minute interval):
        current 12:03:45  →  returns 75  (reads at 12:05:00)
        current 12:05:00  →  returns  0  (read right now)
        current 12:04:59  →  returns  1  (reads at 12:05:00)
    """
    now = rtc.datetime
    mins_past = now.tm_min % READ_INTERVAL_MINUTES
    mins_left = (READ_INTERVAL_MINUTES - mins_past) % READ_INTERVAL_MINUTES
    if mins_left == 0 and now.tm_sec > 0:
        mins_left = READ_INTERVAL_MINUTES
    return float(mins_left * 60 - now.tm_sec)


# ---------------------------------------------------------------------------
# Individual sensor coroutines
# ---------------------------------------------------------------------------


async def _read_barometric(reader: BarometricReader) -> None:
    data = reader.read()
    if _gps_altitude_m is not None:
        data.sea_level_pressure = BarometricReader.sea_level_pressure_from_altitude(
            data.pressure, _gps_altitude_m
        )
    print(data)
    await asyncio.sleep(0)


async def _read_temp_humidity(reader: TempHumidityReader) -> None:
    print(reader.read())
    await asyncio.sleep(0)


async def _read_air_quality(reader: AirQualityReader) -> None:
    print(reader.read())
    await asyncio.sleep(0)


async def _read_rainfall(reader: RainfallReader) -> None:
    data = reader.read()
    data.rainfall_window_mm = reader.read_window(hours=1)
    print(data)
    await asyncio.sleep(0)


async def _read_wind_direction(reader: WindDirectionReader) -> None:
    print(reader.read())
    await asyncio.sleep(0)


async def _read_wind_speed(reader: WindSpeedReader) -> None:
    print(reader.read())
    await asyncio.sleep(0)


async def _read_illuminance(reader: IlluminanceReader) -> None:
    print(reader.read())
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# GPS background task
# ---------------------------------------------------------------------------


async def gps_task(gps: GpsReader) -> None:
    """Poll the GPS UART continuously and update the shared altitude.

    Runs forever alongside the sensor cycle task. Yields control between
    each update so the scheduler can run other tasks.
    """
    global _gps_altitude_m  # noqa: PLW0603
    while True:
        gps.update()
        data = gps.read()
        if data.has_fix and data.altitude_m is not None:
            _gps_altitude_m = data.altitude_m
        await asyncio.sleep(_GPS_UPDATE_INTERVAL_S)


# ---------------------------------------------------------------------------
# Sensor cycle task
# ---------------------------------------------------------------------------


async def sensor_cycle_task(
    rtc: adafruit_pcf8523.PCF8523,
    baro: BarometricReader,
    temp_hum: TempHumidityReader,
    aq: AirQualityReader,
    rain: RainfallReader,
    wind_dir: WindDirectionReader,
    wind_spd: WindSpeedReader,
    illum: IlluminanceReader,
) -> None:
    """Sleep until the next interval boundary, then read all sensors."""
    while True:
        sleep_s = _seconds_until_next_interval(rtc)
        now = rtc.datetime
        print(
            f"[{now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d}]"
            f" next read in {sleep_s:.0f}s"
        )
        await asyncio.sleep(sleep_s)

        now = rtc.datetime
        print(f"--- reading sensors at {now.tm_hour:02d}:{now.tm_min:02d} ---")

        # I2C sensors: blocking but fast (~5-20 ms each).
        # gather() provides structure; reads complete sequentially.
        await asyncio.gather(
            _read_barometric(baro),
            _read_temp_humidity(temp_hum),
            _read_air_quality(aq),
            _read_rainfall(rain),
        )

        # RS485 sensors: MUST remain sequential — one shared UART bus.
        # gather() is used here for consistency only.
        await asyncio.gather(
            _read_wind_direction(wind_dir),
            _read_wind_speed(wind_spd),
            _read_illuminance(illum),
        )


# ---------------------------------------------------------------------------
# Hardware initialisation and entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    # Shared I2C bus (STEMMA QT / FeatherWing headers)
    i2c = busio.I2C(board.SCL, board.SDA)

    # RTC — PCF8523 on Adalogger FeatherWing
    rtc = adafruit_pcf8523.PCF8523(i2c)

    # GPS — Ultimate GPS FeatherWing on UART1
    gps_uart = busio.UART(board.TX, board.RX, baudrate=_GPS_BAUD, timeout=0.1)
    gps = GpsReader(gps_uart)
    print("Waiting for GPS fix …")
    fixed = await gps.wait_for_fix(timeout_s=120)
    if fixed:
        print(f"GPS fix acquired: {gps.read()}")
    else:
        print("GPS fix timeout — continuing without fix")

    # RS485 MAX3485 — UART2 + direction-control pin
    rs485_uart = busio.UART(board.A0, board.A1, baudrate=_RS485_BAUD, timeout=_RS485_TIMEOUT_S)
    de_pin = digitalio.DigitalInOut(board.D5)
    de_pin.direction = digitalio.Direction.OUTPUT

    # Sensor readers
    baro = BarometricReader(i2c)
    temp_hum = TempHumidityReader(i2c)
    aq = AirQualityReader(i2c)
    rain = RainfallReader(i2c)
    wind_dir = WindDirectionReader(rs485_uart, de_pin, address=_WIND_DIR_ADDR)
    wind_spd = WindSpeedReader(rs485_uart, de_pin, address=_WIND_SPD_ADDR)
    illum = IlluminanceReader(rs485_uart, de_pin, address=_ILLUMINANCE_ADDR)

    print("FeatherWeather ready")

    await asyncio.gather(
        asyncio.create_task(gps_task(gps)),
        asyncio.create_task(
            sensor_cycle_task(rtc, baro, temp_hum, aq, rain, wind_dir, wind_spd, illum)
        ),
    )


asyncio.run(main())
