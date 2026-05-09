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
    is obtained via board.STEMMA_I2C().

Pin assignments (adjust to match your wiring):
    I2C  SCL / SDA        board.SCL / board.SDA   (STEMMA QT / FeatherWing)
    SPI  SCK / MOSI / MISO board.SCK / MOSI / MISO (SD card on Adalogger)
    SPI  CS               board.D33               (SD chip-select; mounted at /sd)
    UART1 TX / RX         board.TX / board.RX     (GPS FeatherWing, 9600 baud)
    UART2 TX / RX         board.A0 / board.A1     (RS485 MAX3485, 9600 baud)
    DE   MAX3485 DE/~RE   board.D12  (GPIO12 — strapping pin, must be LOW at boot;
                          DE defaults LOW = receive mode, so this is safe)
    OLED TOP    (C)       board.A6                (GPIO37, input-only — next page)
    OLED MIDDLE (B)       board.A7                (GPIO32 — toggle display on/off)
    OLED BOTTOM (A)       board.A8                (GPIO15 — previous page)

Modbus addresses (reprogram conflicting sensors before first use):
    SEN0482 Wind Direction  0x02  (default; run set_address() if conflict)
    SEN0483 Wind Speed      0x03  (default is 0x02 — must reassign to 0x03)
    SEN0644 Illuminance     0x01  (default)
"""

import os
import time

import adafruit_pcf8523.pcf8523 as _pcf8523_mod
import adafruit_sdcard
import board
import busio
import digitalio
import displayio
import storage

from featherweather.display.display_controller import DisplayController
from featherweather.gps.gps_reader import GpsReader
from featherweather.rtc.rtc_sync import sync_rtc_from_ntp
from featherweather.storage.sd_file_server import SdFileServer
from featherweather.sensors.air_quality.air_quality_reader import AirQualityReader
from featherweather.sensors.barometric.barometric_reader import BarometricReader
from featherweather.sensors.illuminance.illuminance_reader import IlluminanceReader
from featherweather.sensors.rainfall.rainfall_reader import RainfallReader
from featherweather.sensors.temp_humidity.temp_humidity_reader import TempHumidityReader
from featherweather.sensors.microphone.microphone_reader import MicrophoneReader
from featherweather.sensors.wind_direction.wind_direction_reader import WindDirectionReader
from featherweather.sensors.wind_speed.wind_speed_reader import WindSpeedReader

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

READ_INTERVAL_MINUTES: int = int(os.getenv("READ_INTERVAL_MINUTES") or 5)
_NTP_TZ_OFFSET: int = int(os.getenv("NTP_TIMEZONE_OFFSET") or 0)

_GPS_BAUD: int = int(os.getenv("GPS_BAUD") or 9600)
_GPS_POLL_INTERVAL_S: float = 0.2   # poll GPS UART at 5 Hz

_RS485_BAUD: int = int(os.getenv("RS485_BAUD") or 9600)
_RS485_TIMEOUT_S: float = int(os.getenv("RS485_TIMEOUT_MS") or 500) / 1000

_LOOP_INTERVAL_S: float = 0.05  # main loop cadence — buttons polled at ~20 Hz

# Modbus slave addresses — change if you have reassigned them
_WIND_DIR_ADDR: int = 0x02
_WIND_SPD_ADDR: int = 0x03  # reprogrammed from default 0x02 to avoid conflict
_ILLUMINANCE_ADDR: int = 0x01

# ---------------------------------------------------------------------------
# Shared GPS altitude (updated each GPS poll, consumed by sensor cycle)
# ---------------------------------------------------------------------------

_gps_altitude_m: float | None = None


# ---------------------------------------------------------------------------
# Fault-tolerant sensor initialisation helper
# ---------------------------------------------------------------------------


def _try_init(label: str, factory):
    """Call factory(); return the result, or None if it raises.

    Logs a warning so missing hardware is visible in the serial console
    without aborting the rest of startup.
    """
    try:
        obj = factory()
        print(f"[init] {label} OK")
        return obj
    except Exception as exc:  # noqa: BLE001
        print(f"[init] {label} SKIPPED ({type(exc).__name__}: {exc})")
        return None


# ---------------------------------------------------------------------------
# Scheduling helpers
# ---------------------------------------------------------------------------


def _seconds_until_next_interval(rtc: _pcf8523_mod.PCF8523) -> float:
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
# GPS poll helper
# ---------------------------------------------------------------------------


def _poll_gps(gps: GpsReader, display: DisplayController) -> None:
    """Process one round of GPS UART bytes and push state to the display."""
    global _gps_altitude_m  # noqa: PLW0603
    gps.update()
    data = gps.read()
    if data.has_fix and data.altitude_m is not None:
        _gps_altitude_m = data.altitude_m
    display.state.gps_has_fix = data.has_fix
    display.state.gps_satellites = data.satellites
    display.state.gps_altitude_m = data.altitude_m
    display.state.gps_latitude = data.latitude
    display.state.gps_longitude = data.longitude
    if data.timestamp_utc is not None:
        display.state.gps_utc_h = data.timestamp_utc.tm_hour
        display.state.gps_utc_m = data.timestamp_utc.tm_min
        display.state.gps_utc_s = data.timestamp_utc.tm_sec
        display.state.gps_utc_day = data.timestamp_utc.tm_mday
        display.state.gps_utc_month = data.timestamp_utc.tm_mon
        display.state.gps_utc_year = data.timestamp_utc.tm_year


# ---------------------------------------------------------------------------
# Sensor cycle
# ---------------------------------------------------------------------------


def _run_sensor_cycle(
    rtc: _pcf8523_mod.PCF8523,
    baro,
    temp_hum,
    aq,
    rain,
    wind_dir,
    wind_spd,
    illum,
    mic,
    display: DisplayController,
) -> None:
    """Read all available sensors sequentially and refresh the display.

    Any reader that is None was unavailable at startup and is silently skipped.
    """
    now = rtc.datetime
    print(f"--- reading sensors at {now.tm_hour:02d}:{now.tm_min:02d} ---")

    # I2C sensors
    if baro is not None:
        try:
            data = baro.read()
            if _gps_altitude_m is not None:
                data.sea_level_pressure = BarometricReader.sea_level_pressure_from_altitude(
                    data.pressure, _gps_altitude_m
                )
            print(data)
            display.state.pressure_hpa = data.pressure
            display.state.baro_temp_c = data.temperature
            display.state.sea_level_pressure_hpa = data.sea_level_pressure
        except Exception as exc:  # noqa: BLE001
            print(f"baro error: {exc}")

    if temp_hum is not None:
        try:
            data = temp_hum.read()
            print(data)
            display.state.temp_c = data.temperature
            display.state.humidity_pct = data.relative_humidity
        except Exception as exc:  # noqa: BLE001
            print(f"temp/hum error: {exc}")

    if aq is not None:
        try:
            data = aq.read()
            print(data)
            display.state.pm_1_0 = data.pm_1_0_atm
            display.state.pm_2_5 = data.pm_2_5_atm
            display.state.pm_10 = data.pm_10_atm
        except Exception as exc:  # noqa: BLE001
            print(f"air quality error: {exc}")

    if rain is not None:
        try:
            data = rain.read()
            data.rainfall_window_mm = rain.read_window(hours=1)
            print(data)
            display.state.rain_cumulative_mm = data.cumulative_rainfall_mm
            display.state.rain_window_mm = data.rainfall_window_mm
        except Exception as exc:  # noqa: BLE001
            print(f"rainfall error: {exc}")

    # RS485 sensors — sequential, share one UART bus
    if wind_dir is not None:
        try:
            data = wind_dir.read()
            print(data)
            display.state.wind_dir_deg = data.degrees
            display.state.wind_dir_label = data.direction_label
        except Exception as exc:  # noqa: BLE001
            print(f"wind dir error: {exc}")

    if wind_spd is not None:
        try:
            data = wind_spd.read()
            print(data)
            display.state.wind_speed_ms = data.speed_ms
            display.state.wind_beaufort = data.beaufort
        except Exception as exc:  # noqa: BLE001
            print(f"wind speed error: {exc}")

    if illum is not None:
        try:
            data = illum.read()
            print(data)
            display.state.lux = data.lux
        except Exception as exc:  # noqa: BLE001
            print(f"illuminance error: {exc}")

    if mic is not None:
        try:
            data = mic.read()
            print(data)
            display.state.db_spl = data.db_spl
        except Exception as exc:  # noqa: BLE001
            print(f"mic error: {exc}")

    display.state.last_read_s = time.monotonic()
    display.render()


# ---------------------------------------------------------------------------
# Hardware initialisation and main loop
# ---------------------------------------------------------------------------


def main() -> None:
    # Release any display left active by the previous code.py run.
    # This must happen before busio.I2C() is created — the previous run's
    # I2CDisplayBus holds SCL/SDA until release_displays() is called.
    displayio.release_displays()

    # Shared I2C bus for display + all I2C sensors
    i2c = busio.I2C(board.SCL, board.SDA)

    # OLED FeatherWing #4650 — DisplayController also calls release_displays()
    # internally (harmless second call) then claims the display bus.
    display = DisplayController(i2c)
    display.render()

    # SD card — boot.py and code.py run in separate Python VMs.  boot.py's
    # C-level VFS registration at /sd persists across the VM boundary, but its
    # underlying SPI peripheral is released when the boot VM ends.  That leaves
    # a stale mount whose block reads silently return empty data.  Fix: always
    # umount any stale /sd entry before creating a fresh, live mount.
    _sd_spi = _sd_cs = _sdcard = _sd_vfs = None
    try:
        storage.umount("/sd")
        print("SD card: removed stale /sd mount")
    except OSError:
        pass  # not mounted — nothing to clean up
    try:
        _sd_spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
        _sd_cs  = digitalio.DigitalInOut(board.D33)
        _sdcard = adafruit_sdcard.SDCard(_sd_spi, _sd_cs)
        _sd_vfs = storage.VfsFat(_sdcard)
        storage.mount(_sd_vfs, "/sd")
        _sd_entries = os.listdir("/sd")
        print(f"SD card mounted at /sd — {len(_sd_entries)} file(s)")
    except OSError as exc:
        print(f"SD card mount failed: {exc}")

    # RTC — PCF8523 on Adalogger FeatherWing; sync time from NTP on every boot
    rtc = _pcf8523_mod.PCF8523(i2c)
    sync_rtc_from_ntp(rtc, tz_offset=_NTP_TZ_OFFSET)

    # GPS — Ultimate GPS FeatherWing on UART1
    gps_uart = busio.UART(board.TX, board.RX, baudrate=_GPS_BAUD, timeout=0.1)
    gps = GpsReader(gps_uart)
    print("Waiting for GPS fix …")
    fixed = gps.wait_for_fix(timeout_s=120)
    if fixed:
        print(f"GPS fix acquired: {gps.read()}")
    else:
        print("GPS fix timeout — continuing without fix")

    # RS485 MAX3485 — UART2 + direction-control pin (optional: skip if pins unavailable)
    rs485_uart = None
    de_pin = None
    try:
        rs485_uart = busio.UART(board.A0, board.A1, baudrate=_RS485_BAUD, timeout=_RS485_TIMEOUT_S)
        de_pin = digitalio.DigitalInOut(board.D12)
        de_pin.direction = digitalio.Direction.OUTPUT
        print("[init] RS485 UART OK")
    except Exception as exc:  # noqa: BLE001
        print(f"[init] RS485 UART SKIPPED ({type(exc).__name__}: {exc})")

    # I2C sensor readers — each is None if the hardware is absent
    baro     = _try_init("BMP390  (barometric)",    lambda: BarometricReader(i2c))
    temp_hum = _try_init("SHTC3   (temp/humidity)", lambda: TempHumidityReader(i2c))
    aq       = _try_init("HM3301  (air quality)",   lambda: AirQualityReader(i2c))
    rain     = _try_init("SEN0575 (rainfall)",      lambda: RainfallReader(i2c))

    # I2S microphone — None if audiobusio.I2SIn is unsupported on this build
    mic = _try_init("SPH0645 (microphone)",     lambda: MicrophoneReader())

    # RS485 sensor readers — each is None if the UART setup failed or sensor absent
    if rs485_uart is not None and de_pin is not None:
        wind_dir = _try_init(
            "SEN0482 (wind direction)",
            lambda: WindDirectionReader(rs485_uart, de_pin, address=_WIND_DIR_ADDR),
        )
        wind_spd = _try_init(
            "SEN0483 (wind speed)",
            lambda: WindSpeedReader(rs485_uart, de_pin, address=_WIND_SPD_ADDR),
        )
        illum = _try_init(
            "SEN0644 (illuminance)",
            lambda: IlluminanceReader(rs485_uart, de_pin, address=_ILLUMINANCE_ADDR),
        )
    else:
        wind_dir = wind_spd = illum = None

    # SD file server — non-blocking HTTP server on port 8080 for /sd/ access
    # Allows fetch-sd to pull files while code.py is running (no reboot needed)
    try:
        import wifi as _wifi  # noqa: PLC0415
        sd_server = SdFileServer(_wifi.radio, port=8080)
        ip = _wifi.radio.ipv4_address
        display.state.ip_address = str(ip) if ip is not None else None
        print(f"[init] IP address: {display.state.ip_address}")
    except Exception as exc:  # noqa: BLE001
        print(f"[init] SD file server SKIPPED ({exc})")
        sd_server = None

    print("FeatherWeather ready")

    # Read sensors immediately so the display shows live data from the first button press
    _run_sensor_cycle(rtc, baro, temp_hum, aq, rain, wind_dir, wind_spd, illum, mic, display)

    # Schedule subsequent reads at fixed interval boundaries
    next_read_mono = time.monotonic() + _seconds_until_next_interval(rtc)
    last_gps_poll = 0.0
    now = rtc.datetime
    print(
        f"[{now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d}]"
        f" next read in {next_read_mono - time.monotonic():.0f}s"
    )

    while True:
        now_mono = time.monotonic()

        # GPS — poll at 5 Hz to keep UART buffer clear and state fresh
        if now_mono - last_gps_poll >= _GPS_POLL_INTERVAL_S:
            last_gps_poll = now_mono
            _poll_gps(gps, display)

        # Buttons — polled every loop iteration (~20 Hz)
        display.poll_buttons()

        # SD file server — accept one pending HTTP connection if any
        if sd_server is not None:
            sd_server.poll()

        # Sensor cycle — fires when the next interval boundary is reached
        if now_mono >= next_read_mono:
            _run_sensor_cycle(rtc, baro, temp_hum, aq, rain, wind_dir, wind_spd, illum, mic, display)
            next_read_mono = time.monotonic() + _seconds_until_next_interval(rtc)
            now = rtc.datetime
            print(
                f"[{now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d}]"
                f" next read in {_seconds_until_next_interval(rtc):.0f}s"
            )

        time.sleep(_LOOP_INTERVAL_S)


main()
