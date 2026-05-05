"""FeatherWeather Diagnostic Mode — I2C / GPS / SD card self-test.

Deploy to CIRCUITPY/ as code.py to run instead of normal operation:

    python scripts/deploy.py --diagnostic

The featherweather package must be present in CIRCUITPY/lib/ (run
``python scripts/deploy.py`` at least once beforehand to install it).

Hardware checked
----------------
    BMP390       barometric pressure + temperature  I2C 0x77
    HM3301       PM air quality                     I2C 0x40  (bus <= 20 kHz)
    SHTC3        temperature / humidity             I2C 0x70
    PCF8523      Adalogger FeatherWing RTC          I2C 0x68
    Ultimate GPS UART NMEA activity + optional fix  board.TX / board.RX
    SD card      Adalogger FeatherWing SPI storage  board.D10 CS

Output
------
    Serial console              live progress with [PASS] / [FAIL] per device
    /sd/diag_YYYYMMDD_HHMMSS.txt  persisted report (diag_unknown.txt if RTC fails)
"""

import time

import board
import busio

# ---------------------------------------------------------------------------
# Optional library imports — loaded lazily inside each check function so a
# missing library causes a [FAIL] for that device rather than crashing the
# whole script and putting the device in a reboot loop.
# ---------------------------------------------------------------------------

# These built-in / always-present modules are safe to import at the top level:
#   time, board, busio, digitalio, storage  (all built into CircuitPython firmware)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_SD_CS_PIN = board.D33    # defined in boot.py — kept here for reference only
_GPS_BAUD: int = 9600
_GPS_NMEA_CHECK_S: float = 5.0    # seconds to wait for the first NMEA sentence
_GPS_FIX_TIMEOUT_S: float = 30.0  # seconds to attempt a GPS fix
_I2C_FREQ_HZ: int = 20_000        # HM3301 maximum; all other I2C devices tolerate it
_NTP_TZ_OFFSET: int = int(__import__("os").getenv("NTP_TIMEZONE_OFFSET") or 0)

_KNOWN_I2C_ADDRS: dict = {
    0x40: "HM3301 (Air Quality)",
    0x68: "PCF8523 (Adalogger RTC)",
    0x70: "SHTC3 (Temp/Humidity)",
    0x77: "BMP390 (Barometric)",
}

# ---------------------------------------------------------------------------
# Report buffer — all output is echoed to serial and buffered for SD write
# ---------------------------------------------------------------------------

_report_lines: list = []


def _log(msg: str = "") -> None:
    print(msg)
    _report_lines.append(msg)


def _section(title: str) -> None:
    _log()
    _log("=" * 54)
    _log(f"  {title}")
    _log("=" * 54)


def _result(label: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    _log(f"  [{status}] {label}{suffix}")


# ---------------------------------------------------------------------------
# SD card — verify the mount that boot.py established
# ---------------------------------------------------------------------------


def _verify_sd() -> bool:
    _section("Adalogger SD Card")
    # boot.py mounts the SD card before code.py runs.
    # Verify by checking /sd is in the root listing and writing a sentinel file.
    try:
        import os  # noqa: PLC0415
        if "sd" not in os.listdir("/"):
            _result("SD card mount", False, "/sd not present — check boot.py")
            return False
        # Write a sentinel file to confirm the card is writable
        sentinel = "/sd/mounted.txt"
        f = open(sentinel, "w")
        f.write("SD card mounted OK\n")
        f.flush()
        f.close()
        _result("SD card mount", True, "mounted at /sd, write verified")
        return True
    except Exception as exc:  # noqa: BLE001
        _result("SD card mount", False, str(exc))
        return False


# ---------------------------------------------------------------------------
# PCF8523 RTC (Adalogger FeatherWing)
# ---------------------------------------------------------------------------


def _check_rtc(i2c):
    _section("PCF8523 RTC (Adalogger FeatherWing)")
    try:
        from adafruit_pcf8523.pcf8523 import PCF8523  # noqa: PLC0415
        rtc = PCF8523(i2c)
        dt = rtc.datetime
        ts = (
            f"{dt.tm_year}-{dt.tm_mon:02d}-{dt.tm_mday:02d}"
            f" {dt.tm_hour:02d}:{dt.tm_min:02d}:{dt.tm_sec:02d}"
        )
        _result("PCF8523 init + read", True, ts)
        return rtc
    except Exception as exc:  # noqa: BLE001
        _result("PCF8523 init + read", False, str(exc))
        return None


# ---------------------------------------------------------------------------
# I2C bus scan
# ---------------------------------------------------------------------------


def _scan_i2c(i2c) -> list:
    _section("I2C Bus Scan")
    while not i2c.try_lock():
        pass
    try:
        found = i2c.scan()
    finally:
        i2c.unlock()

    if found:
        _log(f"  Found {len(found)} device(s): {[hex(a) for a in found]}")
        for addr in found:
            label = _KNOWN_I2C_ADDRS.get(addr, "Unknown device")
            _log(f"    {hex(addr):<6}  {label}")
    else:
        _log("  No I2C devices found — check wiring and bus frequency!")
    return found


# ---------------------------------------------------------------------------
# Sensor checks
# ---------------------------------------------------------------------------


def _check_bmp390(i2c):
    _section("BMP390 Barometric Sensor")
    try:
        from featherweather.sensors.barometric.barometric_reader import BarometricReader  # noqa: PLC0415
        reader = BarometricReader(i2c)
        data = reader.read()
        _result(
            "BMP390 init + read",
            True,
            f"pressure={data.pressure:.2f} hPa  temp={data.temperature:.2f} C",
        )
        return data
    except Exception as exc:  # noqa: BLE001
        _result("BMP390 init + read", False, str(exc))
        return None


def _check_shtc3(i2c):
    _section("SHTC3 Temperature / Humidity Sensor")
    try:
        from featherweather.sensors.temp_humidity.temp_humidity_reader import TempHumidityReader  # noqa: PLC0415
        reader = TempHumidityReader(i2c)
        data = reader.read()
        _result(
            "SHTC3 init + read",
            True,
            f"temp={data.temperature:.2f} C  humidity={data.relative_humidity:.1f} %RH",
        )
        return data
    except Exception as exc:  # noqa: BLE001
        _result("SHTC3 init + read", False, str(exc))
        return None


def _check_hm3301(i2c):
    _section("HM3301 Air Quality Sensor")
    try:
        from featherweather.sensors.air_quality.air_quality_reader import AirQualityReader  # noqa: PLC0415
        # HM3301 needs a few seconds to stabilize after power-on before
        # it produces valid CRC-passing frames.
        _log("  Waiting 3s for HM3301 warmup …")
        time.sleep(3.0)
        reader = AirQualityReader(i2c, retry=8, wait_sec=0.3)
        data = reader.read()
        _result(
            "HM3301 init + read",
            True,
            (
                f"PM1.0={data.pm_1_0_atm} "
                f"PM2.5={data.pm_2_5_atm} "
                f"PM10={data.pm_10_atm} ug/m3"
            ),
        )
        return data
    except Exception as exc:  # noqa: BLE001
        _result("HM3301 init + read", False, str(exc))
        return None


# ---------------------------------------------------------------------------
# GPS check — synchronous polling, uses adafruit_gps directly to avoid
# importing GpsReader (which pulls in asyncio for wait_for_fix)
# ---------------------------------------------------------------------------

# NMEA sentences: GGA (position/altitude) + RMC (speed/heading/time) only
_PMTK_SET_NMEA_OUTPUT = b"PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0"
_PMTK_SET_UPDATE_RATE = b"PMTK220,1000"


def _check_gps():
    _section("Ultimate GPS FeatherWing")

    try:
        import adafruit_gps  # noqa: PLC0415
        uart = busio.UART(board.TX, board.RX, baudrate=_GPS_BAUD, timeout=0.1)
        gps = adafruit_gps.GPS(uart, debug=False)
        gps.send_command(_PMTK_SET_NMEA_OUTPUT)
        gps.send_command(_PMTK_SET_UPDATE_RATE)
        _result("GPS UART init", True)
    except Exception as exc:  # noqa: BLE001
        _result("GPS UART init", False, str(exc))
        return None, None

    # Confirm the GPS module is alive by watching for a parsed NMEA sentence
    _log(f"  Checking for NMEA sentences ({_GPS_NMEA_CHECK_S:.0f}s) …")
    nmea_seen = False
    deadline = time.monotonic() + _GPS_NMEA_CHECK_S
    while time.monotonic() < deadline:
        if gps.update():
            nmea_seen = True
            break
        time.sleep(0.1)

    _result(
        "GPS NMEA activity",
        nmea_seen,
        "" if nmea_seen else "no sentences received — check antenna/wiring",
    )

    # Attempt a fix — cold-start indoors will likely time out, and that is OK
    _log(f"  Waiting for GPS fix (up to {_GPS_FIX_TIMEOUT_S:.0f}s) …")
    deadline = time.monotonic() + _GPS_FIX_TIMEOUT_S
    while not gps.has_fix and time.monotonic() < deadline:
        gps.update()
        time.sleep(0.2)

    if gps.has_fix:
        ts = (
            f"{gps.timestamp_utc.tm_hour:02d}:{gps.timestamp_utc.tm_min:02d}:"
            f"{gps.timestamp_utc.tm_sec:02d}Z"
            if gps.timestamp_utc
            else "n/a"
        )
        _result(
            "GPS fix",
            True,
            (
                f"lat={gps.latitude:.6f}  lon={gps.longitude:.6f}"
                f"  alt={gps.altitude_m:.1f}m  sats={gps.satellites}  utc={ts}"
            ),
        )
    else:
        # No fix is not a hardware failure — NMEA activity is the real indicator
        _result(
            "GPS fix (no fix yet)",
            nmea_seen,
            "module alive, needs clear sky view" if nmea_seen else "GPS module unresponsive",
        )

    return gps, gps.has_fix


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _write_report(rtc) -> None:
    if rtc is not None:
        dt = rtc.datetime
        stamped = (
            f"/sd/diag_{dt.tm_year}{dt.tm_mon:02d}{dt.tm_mday:02d}"
            f"_{dt.tm_hour:02d}{dt.tm_min:02d}{dt.tm_sec:02d}.txt"
        )
    else:
        stamped = "/sd/diag_unknown.txt"

    # Always write diag_latest.txt so the file is easy to find, plus the
    # timestamped copy when the RTC is valid.
    targets = ["/sd/diag_latest.txt"]
    if stamped != "/sd/diag_unknown.txt":
        targets.append(stamped)
    else:
        targets = [stamped]

    for filename in targets:
        try:
            with open(filename, "w") as f:
                for line in _report_lines:
                    # Write only ASCII — strip non-ASCII chars to avoid
                    # FAT write errors on some CircuitPython builds
                    safe = "".join(c if ord(c) < 128 else "?" for c in line)
                    f.write(safe + "\n")
                f.flush()
            print(f"Report saved -> {filename}")
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to write {filename}: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_log("FeatherWeather Diagnostic Mode")
_log(f"Started  monotonic={time.monotonic():.1f}s")
_log("Checking: BMP390, HM3301, SHTC3, PCF8523 RTC, GPS, SD card")

# I2C bus at 20 kHz — required for HM3301; all other I2C devices tolerate this
i2c = busio.I2C(board.SCL, board.SDA, frequency=_I2C_FREQ_HZ)

# SD card — boot.py mounted it before code.py ran; verify it is writable
sd_ok = _verify_sd()

# RTC provides the timestamp used in the report filename
rtc = _check_rtc(i2c)

# Sync RTC from NTP — fixes unset / drifted time before the filename is stamped
_section("NTP Time Sync")
if rtc is not None:
    try:
        from featherweather.rtc.rtc_sync import sync_rtc_from_ntp  # noqa: PLC0415
        ntp_ok = sync_rtc_from_ntp(rtc, tz_offset=_NTP_TZ_OFFSET)
        _result("NTP sync", ntp_ok, "" if ntp_ok else "check WiFi credentials in settings.toml")
    except Exception as _exc:  # noqa: BLE001
        _result("NTP sync", False, str(_exc))
else:
    _result("NTP sync", False, "skipped — RTC not available")

# Enumerate all I2C addresses before probing individual sensors
i2c_addrs = _scan_i2c(i2c)

# Individual sensor checks
baro_data = _check_bmp390(i2c)
th_data = _check_shtc3(i2c)
aq_data = _check_hm3301(i2c)

# GPS is on UART, not I2C
gps, gps_has_fix = _check_gps()

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

_section("Diagnostic Summary")

gps_alive = gps is not None

_result("SD card",             sd_ok)
_result("PCF8523 RTC",         rtc is not None)
_result("BMP390 Barometric",   baro_data is not None)
_result("SHTC3 Temp/Humidity", th_data is not None)
_result("HM3301 Air Quality",  aq_data is not None)

if gps_has_fix:
    _result("GPS", True, "fix acquired")
elif gps_alive:
    _result("GPS module", True, "NMEA active — no fix yet (normal indoors/cold start)")
else:
    _result("GPS", False, "no response")

all_ok = all([
    sd_ok,
    rtc is not None,
    baro_data is not None,
    th_data is not None,
    aq_data is not None,
    gps_alive,
])

_log()
_log("Overall: " + ("ALL SYSTEMS GO" if all_ok else "ISSUES DETECTED — see details above"))
_log()

# ---------------------------------------------------------------------------
# Persist report to SD card
# ---------------------------------------------------------------------------

if sd_ok:
    _write_report(rtc)
    # SD card stays mounted so files are visible via the web workflow browser
    # at http://<device-ip>/fs/#/sd/ until the next reset.
else:
    _log("SD card unavailable — report not saved to disk")

_log("Diagnostic complete.")
