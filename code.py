"""FeatherWeather – main entry point for ESP32 Feather V2.

Deploy this file to the root of the CircuitPython device (CIRCUITPY/code.py).
The featherweather package must be copied to CIRCUITPY/lib/featherweather/.

Scheduling strategy:
    Sensor reads happen at fixed N-minute boundaries (cron-style) aligned to
    wall-clock time from the PCF8523 RTC (e.g. :00, :05, :10 ... :55).
    Between reads the main loop polls the GPS UART and the OLED buttons at
    20 Hz using time.monotonic() timestamps — no asyncio required.

    Initialisation order matters: DisplayController() must be created first so
    that displayio.release_displays() frees SCL/SDA before the shared I2C bus
    is obtained via featherweather.hardware.i2c_bus.

Pin assignments (adjust to match your wiring):
    I2C  SCL / SDA         board.SCL / board.SDA   (STEMMA QT / FeatherWing)
    SPI  SCK / MOSI / MISO board.SCK / MOSI / MISO (SD card on Adalogger)
    SPI  CS                board.D33               (SD chip-select; mounted at /sd)
    UART1 TX / RX          board.TX / board.RX     (GPS FeatherWing, 9600 baud)
    UART2 TX / RX          board.A0 / board.A1     (RS485 MAX3485, 9600 baud)
    DE   MAX3485 DE/~RE    board.D12  (GPIO12 — strapping pin, LOW at boot; safe default)
    OLED TOP    (C)        board.A6                (GPIO37, input-only — next page)
    OLED MIDDLE (B)        board.A7                (GPIO32 — toggle display on/off)
    OLED BOTTOM (A)        board.A8                (GPIO15 — previous page)

Modbus addresses (reprogram conflicting sensors before first use):
    SEN0482 Wind Direction  0x02  (default)
    SEN0483 Wind Speed      0x03  (reprogrammed from default 0x02)
    SEN0644 Illuminance     0x01  (default)

Environment variables (settings.toml):
    READ_INTERVAL_S         sensor read cadence in seconds    (default 300)
    GPS_BAUD                GPS UART baud rate                (default 9600)
    RS485_BAUD              RS485 UART baud rate              (default 9600)
    RS485_TIMEOUT_MS        RS485 read timeout                (default 500)
    I2C_FREQ_HZ             I2C bus frequency                 (default 20000)
    NTP_TIMEZONE / NTP_TIMEZONE_OFFSET  local timezone        (default UTC)
    WIND_DIR_ADDR           SEN0482 Modbus address            (default 0x02)
    WIND_SPD_ADDR           SEN0483 Modbus address            (default 0x03)
    ILLUMINANCE_ADDR        SEN0644 Modbus address            (default 0x01)
"""

import gc
import os
import time

from featherweather.display.display_controller import DisplayController
from featherweather.gps.gps_reader import GpsReader
from featherweather.hardware.sd_card import mount_sd
from featherweather.rtc.rtc_sync import get_rtc, sync_rtc_from_ntp
from featherweather.sensors.air_quality.air_quality_reader import AirQualityReader
from featherweather.sensors.barometric.barometric_reader import BarometricReader
from featherweather.sensors.illuminance.illuminance_reader import IlluminanceReader
from featherweather.sensors.microphone.microphone_reader import MicrophoneReader
from featherweather.sensors.rainfall.rainfall_reader import RainfallReader
from featherweather.sensors.sensor_base import SensorBase
from featherweather.sensors.temp_humidity.temp_humidity_reader import TempHumidityReader
from featherweather.sensors.wind_direction.wind_direction_reader import WindDirectionReader
from featherweather.sensors.wind_speed.wind_speed_reader import WindSpeedReader
from featherweather.storage.sd_file_server import SdFileServer
from featherweather.storage.weather_payload import WeatherPayload
from featherweather.system.system_reader import SystemReader

# ---------------------------------------------------------------------------
# Configuration (read from settings.toml; defaults used when absent)
# ---------------------------------------------------------------------------

READ_INTERVAL_S: int = int(os.getenv("READ_INTERVAL_S") or 300)

_LOOP_INTERVAL_S: float = 0.05    # main loop cadence — buttons polled at ~20 Hz
_GPS_POLL_INTERVAL_S: float = 0.2  # poll GPS UART at 5 Hz


# ---------------------------------------------------------------------------
# Fault-tolerant sensor initialisation helper
# ---------------------------------------------------------------------------


def _try_init(label: str, factory):
    """Call factory(); return the result, or None if it raises."""
    try:
        obj = factory()
        print(f"[init] {label} OK")
        return obj
    except Exception as exc:  # noqa: BLE001
        print(f"[init] {label} SKIPPED ({type(exc).__name__}: {exc})")
        return None


# ---------------------------------------------------------------------------
# Scheduling helper
# ---------------------------------------------------------------------------


def _seconds_until_next_interval() -> int:
    """Return the configured read interval in seconds."""
    return READ_INTERVAL_S


# ---------------------------------------------------------------------------
# GPS poll — updates only the GPS slice of the current payload (5 Hz)
# ---------------------------------------------------------------------------


def _poll_gps(gps: GpsReader, display: DisplayController) -> None:
    """Read the latest GPS state and push it into the display payload."""
    gps.update()
    display.update_gps(gps.read())


# ---------------------------------------------------------------------------
# Sensor cycle — reads all sensors via the common SensorBase.read(payload)
# ---------------------------------------------------------------------------


def _run_sensor_cycle(
    rtc,
    sensors: list,
    display: DisplayController,
) -> None:
    """Read every available sensor and publish one WeatherPayload to the display.

    Each sensor's ``read(payload)`` populates its own slice of the payload,
    so this function never needs to know about individual sensor types.
    Adding a new sensor requires no changes here — just add it to the list
    passed in from ``main()``.

    Args:
        rtc:     PCF8523 RTC instance for wall-clock timestamps.
        sensors: list of ``SensorBase`` instances (``None`` entries are skipped).
        display: current ``DisplayController``; the completed payload is pushed to it.
    """
    now = rtc.datetime
    print(f"--- reading sensors at {now.tm_hour:02d}:{now.tm_min:02d} ---")

    gc.collect()
    mem_before = gc.mem_alloc()

    # Carry forward GPS and network metadata accumulated between sensor cycles.
    payload = WeatherPayload(
        gps=display.payload.gps,
        ip_address=display.payload.ip_address,
        last_read_s=time.monotonic(),
    )

    for sensor in sensors:
        if sensor is None:
            continue
        _before = gc.mem_alloc()
        try:
            sensor.read(payload)
        except Exception as exc:  # noqa: BLE001
            print(f"[{type(sensor).__name__}] error: {exc}")
        _after = gc.mem_alloc()
        print(f"  [mem] {type(sensor).__name__:<26} {_after - _before:+5d} B")

    display.update(payload)

    gc.collect()
    mem_after = gc.mem_alloc()
    print(
        f"  [mem] cycle total: {mem_after - mem_before:+d} B "
        f"| alloc={mem_after // 1024} KB  free={gc.mem_free() // 1024} KB"
    )


# ---------------------------------------------------------------------------
# Hardware initialisation and main loop
# ---------------------------------------------------------------------------


def main() -> None:
    # DisplayController calls displayio.release_displays() internally and
    # acquires the shared I2C bus — must be first so any display left active
    # by a previous run is freed before sensors access the bus.
    display = DisplayController()
    display.render()

    # SD card — umounts any stale boot.py mount, remounts fresh
    mount_sd()

    # RTC — PCF8523 on Adalogger FeatherWing; sync time from NTP every boot
    rtc = get_rtc()
    sync_rtc_from_ntp(rtc)

    # GPS — Ultimate GPS FeatherWing on UART1 (board.TX / board.RX)
    gps = _try_init("GPS", GpsReader)
    if gps is not None:
        print("Waiting for GPS fix …")
        fixed = gps.wait_for_fix(timeout_s=120)
        if fixed:
            print(f"GPS fix acquired: {gps.read()}")
        else:
            print("GPS fix timeout — continuing without fix")

    # I2C sensor readers — each is None if the hardware is absent
    baro     = _try_init("BMP390  (barometric)",    BarometricReader)
    temp_hum = _try_init("SHTC3   (temp/humidity)", TempHumidityReader)
    aq       = _try_init("HM3301  (air quality)",   AirQualityReader)
    rain     = _try_init("SEN0575 (rainfall)",      RainfallReader)

    # I2S microphone — None if audio_i2sin is not in this firmware build
    mic = _try_init("SPH0645 (microphone)", MicrophoneReader)

    # RS485 sensors — get_rs485() initialises lazily; if it fails (pins
    # unavailable) the exception propagates through _try_init → None
    wind_dir = _try_init("SEN0482 (wind direction)", WindDirectionReader)
    wind_spd = _try_init("SEN0483 (wind speed)",     WindSpeedReader)
    illum    = _try_init("SEN0644 (illuminance)",    IlluminanceReader)

    # System metrics reader — always present (pure software, no hardware dependency)
    sys_reader = SystemReader()

    # Ordered sensor list — passed to every _run_sensor_cycle call.
    # Adding a new sensor only requires inserting it here.
    sensors: list[SensorBase | None] = [
        baro, temp_hum, aq, rain, mic, wind_dir, wind_spd, illum, sys_reader
    ]

    # SD file server — non-blocking HTTP server on port 8080 for /sd/ access
    try:
        import wifi as _wifi  # noqa: PLC0415
        sd_server = SdFileServer(_wifi.radio, port=8080)
        ip = _wifi.radio.ipv4_address
        display.payload.ip_address = str(ip) if ip is not None else None
        print(f"[init] IP address: {display.payload.ip_address}")
    except Exception as exc:  # noqa: BLE001
        print(f"[init] SD file server SKIPPED ({exc})")
        sd_server = None

    print("FeatherWeather ready")

    # Initial sensor read so the display shows live data from the first button press
    _run_sensor_cycle(rtc, sensors, display)

    # Schedule subsequent reads at fixed interval boundaries
    next_read_mono = time.monotonic() + _seconds_until_next_interval()
    last_gps_poll = 0.0
    now = rtc.datetime
    print(
        f"[{now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d}]"
        f" next read in {next_read_mono - time.monotonic():.0f}s"
    )

    while True:
        now_mono = time.monotonic()

        # GPS — poll at 5 Hz to drain UART buffer and keep GPS state fresh
        if gps is not None and now_mono - last_gps_poll >= _GPS_POLL_INTERVAL_S:
            last_gps_poll = now_mono
            _poll_gps(gps, display)

        # Buttons — polled every loop iteration (~20 Hz)
        display.poll_buttons()

        # SD file server — accept one pending HTTP connection if any
        if sd_server is not None:
            sd_server.poll()

        # Sensor cycle — fires when the next interval boundary is reached
        if now_mono >= next_read_mono:
            _run_sensor_cycle(rtc, sensors, display)
            next_read_mono = time.monotonic() + _seconds_until_next_interval()
            now = rtc.datetime
            print(
                f"[{now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d}]"
                f" next read in {READ_INTERVAL_S}s"
            )

        time.sleep(_LOOP_INTERVAL_S)


main()
