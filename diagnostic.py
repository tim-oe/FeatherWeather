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
    NeoPixel                    green flash = pass, red flash = fail per test;
                                summary flash: green×N (all pass), yellow×N
                                (<50% fail), red×N (≥50% fail)
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

_SD_CS_PIN = board.D33

# Set by _verify_sd() so _check_disk() can call sdcard.count() for true capacity.
_sdcard = None
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
# NeoPixel — single built-in pixel for visual pass/fail feedback
# ---------------------------------------------------------------------------

_GREEN  = (0, 50, 0)
_RED    = (50, 0, 0)
_YELLOW = (50, 50, 0)
_OFF    = (0, 0, 0)

_pixel = None
try:
    import neopixel  # noqa: PLC0415
    _pixel = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.2, auto_write=True)
    _pixel[0] = _OFF
except Exception:  # noqa: BLE001
    pass  # NeoPixel unavailable — visual feedback silently skipped


def _flash(color, count: int = 1, on_ms: int = 200, off_ms: int = 100) -> None:
    """Flash the built-in NeoPixel *count* times in *color*."""
    if _pixel is None:
        return
    for _ in range(count):
        _pixel[0] = color
        time.sleep(on_ms / 1000)
        _pixel[0] = _OFF
        time.sleep(off_ms / 1000)

# ---------------------------------------------------------------------------
# Result tracking — updated in the main flow after each top-level test
# ---------------------------------------------------------------------------

_pass_count: int = 0
_fail_count: int = 0


def _track(ok: bool) -> None:
    """Record a top-level test result and flash the NeoPixel accordingly."""
    global _pass_count, _fail_count
    if ok:
        _pass_count += 1
        _flash(_GREEN)
    else:
        _fail_count += 1
        _flash(_RED)


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
    """Mount the SD card and confirm it is writable.

    boot.py no longer mounts the SD card (the mount is torn down between the
    boot.py and code.py VMs).  We mount it here so the card stays available
    for the rest of this script, and we keep a reference to the SDCard object
    so _check_disk() can call sdcard.count() for the true physical capacity.
    """
    global _sdcard  # noqa: PLW0603
    _section("Adalogger SD Card")
    try:
        import os            # noqa: PLC0415
        import storage       # noqa: PLC0415
        import digitalio     # noqa: PLC0415
        import adafruit_sdcard as _asc  # noqa: PLC0415

        spi    = busio.SPI(board.SCK, board.MOSI, board.MISO)
        cs     = digitalio.DigitalInOut(_SD_CS_PIN)
        sdcard = _asc.SDCard(spi, cs)
        vfs    = storage.VfsFat(sdcard)
        storage.mount(vfs, "/sd")
        _sdcard = sdcard  # keep alive for count() in _check_disk()

        _result("SD card mount", True, "mounted at /sd")

        # Confirm card is writable
        with open("/sd/mounted.txt", "w") as f:
            f.write("SD card mounted OK\n")
            f.flush()
        _result("SD card write", True)

        entries = os.listdir("/sd")
        _log(f"    /sd contents: {entries}")
        return True
    except Exception as exc:  # noqa: BLE001
        _result("SD card", False, str(exc))
        return False


# ---------------------------------------------------------------------------
# Disk metrics — os.statvfs() equivalent of df -h
# ---------------------------------------------------------------------------


def _check_disk() -> None:
    """Report filesystem usage for / and /sd.

    Internal flash (/): os.statvfs("/") is used directly — this works correctly
    and reports the CIRCUITPY FAT partition (~3.9 MB of the 8 MB SPI flash; the
    remainder is consumed by the CircuitPython firmware partition).

    SD card (/sd): total capacity comes from sdcard.count() * 512, a direct
    block-device read that bypasses the VFS layer entirely and returns the true
    physical size.  Used bytes are computed by walking the tree with os.listdir
    + os.stat; free is derived as total - used (approximate — ignores FAT
    metadata overhead such as the FAT tables and cluster slack).

    The SDCard object is kept alive by _verify_sd() which mounts the card in
    this code.py VM.  (Mounting in boot.py does not help: boot.py and code.py
    run in separate, consecutive Python VMs, so any mount made in boot.py is
    torn down before code.py starts.)
    """
    _section("Disk Usage")
    import os  # noqa: PLC0415

    def _fmt_bytes(n: float) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    def _walk_used(path: str) -> tuple:
        """Return (file_count, dir_count, total_bytes) for path recursively."""
        files = dirs = nbytes = 0
        try:
            entries = os.listdir(path)
        except Exception:  # noqa: BLE001
            return files, dirs, nbytes
        for name in entries:
            sub = path + "/" + name if path != "/" else "/" + name
            try:
                st = os.stat(sub)
                if st[0] & 0x4000:
                    dirs += 1
                    fc, dc, nb = _walk_used(sub)
                    files += fc
                    dirs  += dc
                    nbytes += nb
                else:
                    files += 1
                    nbytes += st[6]
            except Exception:  # noqa: BLE001
                pass
        return files, dirs, nbytes

    # --- Internal flash (/) ---
    try:
        st    = os.statvfs("/")
        block = st[1]
        total = block * st[2]
        free  = block * st[4]
        used  = total - free
        pct   = (used / total * 100) if total else 0.0
        _log("  Internal flash  /")
        _log(f"    total : {_fmt_bytes(total)}")
        _log(f"    used  : {_fmt_bytes(used)}  ({pct:.0f}%)")
        _log(f"    free  : {_fmt_bytes(free)}")
    except Exception as exc:  # noqa: BLE001
        _log(f"  /  unavailable ({exc})")

    # --- SD card (/sd) ---
    # os.statvfs("/sd") is authoritative here because the mount is owned by
    # this code.py VM.  sdcard.count()*512 reads the CSD register which
    # misreports capacity on SDXC (>32 GB) cards in SPI mode and is not used.
    _log("  SD card  /sd")
    if not _sdcard:
        _log("    unavailable — SD card was not mounted (see SD card section above)")
        return

    sd_total = sd_free = None
    try:
        st = os.statvfs("/sd")
        blk = st[1]
        sd_total = blk * st[2]
        sd_free  = blk * st[4]
    except Exception as exc:  # noqa: BLE001
        _log(f"    statvfs /sd unavailable: {exc}")

    try:
        file_count, dir_count, used_bytes = _walk_used("/sd")
        if sd_total:
            pct = used_bytes / sd_total * 100
            _log(f"    total  : {_fmt_bytes(sd_total)}")
            _log(f"    used   : {_fmt_bytes(used_bytes)}  ({pct:.1f}%)")
            _log(f"    free   : {_fmt_bytes(sd_free)}")
        else:
            _log(f"    used   : {_fmt_bytes(used_bytes)}  (visible files only; total unavailable)")
        _log(f"    files  : {file_count}  dirs : {dir_count}")
    except Exception as exc:  # noqa: BLE001
        _log(f"    unavailable ({exc})")


# ---------------------------------------------------------------------------
# System resources — programmatic htop-style snapshot
# ---------------------------------------------------------------------------


def _check_system() -> None:
    """One-shot snapshot of CPU, memory, runtime, network, firmware.

    CircuitPython on ESP32 doesn't expose the FreeRTOS task table, per-core
    CPU counters, or heap_caps_get_info from the underlying ESP-IDF, so this
    is the maximum useful resolution available from pure Python.
    """
    _section("System Resources")
    import gc                  # noqa: PLC0415
    import os                  # noqa: PLC0415

    # --- Firmware ---
    try:
        u = os.uname()
        _log("  Firmware")
        _log(f"    sysname    : {u.sysname}")
        _log(f"    release    : {u.release}")
        _log(f"    version    : {u.version}")
        _log(f"    machine    : {u.machine}")
    except Exception as exc:  # noqa: BLE001
        _log(f"  Firmware info unavailable ({exc})")

    # --- CPU ---
    def _fmt(val, spec: str, suffix: str = "") -> str:
        # Some CP builds return None instead of raising for unsupported props.
        if val is None:
            return "n/a"
        try:
            return f"{val:{spec}}" + suffix
        except (TypeError, ValueError):
            return "n/a"

    def _safe_get(obj, attr):
        try:
            return getattr(obj, attr)
        except (AttributeError, NotImplementedError, RuntimeError):
            return None

    try:
        import microcontroller  # noqa: PLC0415
        cpu = microcontroller.cpu
        freq_hz = _safe_get(cpu, "frequency")
        temp_c  = _safe_get(cpu, "temperature")
        volts   = _safe_get(cpu, "voltage")
        reset   = _safe_get(cpu, "reset_reason")

        _log("  CPU")
        _log(f"    frequency  : {_fmt(freq_hz / 1_000_000 if freq_hz else None, '.0f', ' MHz')}")
        _log(f"    temperature: {_fmt(temp_c, '.1f', ' C')}")
        _log(f"    voltage    : {_fmt(volts,  '.2f', ' V')}")
        if reset is not None:
            _log(f"    reset cause: {reset}")
        try:
            nvm_len = len(microcontroller.nvm) if microcontroller.nvm is not None else 0
            _log(f"    NVM bytes  : {nvm_len}")
        except (AttributeError, TypeError):
            pass
    except Exception as exc:  # noqa: BLE001
        _log(f"  CPU info unavailable ({exc})")

    # --- Memory (heap) ---
    # Time the gc.collect() call as a proxy for "load":  longer pauses imply a
    # more fragmented or heavily allocated heap.
    t0 = time.monotonic_ns()
    gc.collect()
    gc_ms = (time.monotonic_ns() - t0) / 1_000_000
    free  = gc.mem_free()
    alloc = gc.mem_alloc()
    total = free + alloc
    pct   = (alloc / total * 100) if total else 0.0
    _log("  Memory (heap)")
    _log(f"    total      : {total // 1024} KB")
    _log(f"    allocated  : {alloc // 1024} KB ({pct:.0f}%)")
    _log(f"    free       : {free // 1024} KB")
    _log(f"    gc.collect : {gc_ms:.1f} ms (load proxy)")

    # --- Runtime ---
    try:
        import supervisor  # noqa: PLC0415
        rt = supervisor.runtime
        _log("  Runtime")
        _log(f"    uptime     : {time.monotonic():.0f}s")
        try:
            _log(f"    run_reason : {rt.run_reason}")
        except AttributeError:
            pass
        try:
            _log(f"    safe_mode  : {rt.safe_mode_reason}")
        except AttributeError:
            pass
        try:
            _log(f"    usb conn   : {rt.usb_connected}")
            _log(f"    serial conn: {rt.serial_connected}")
        except AttributeError:
            pass
    except Exception as exc:  # noqa: BLE001
        _log(f"  Runtime info unavailable ({exc})")

    # --- Network (WiFi) ---
    try:
        import wifi  # noqa: PLC0415
        radio = wifi.radio
        _log("  Network (WiFi)")
        if radio.connected:
            _log(f"    ip         : {radio.ipv4_address}")
            try:
                mac = ":".join(f"{b:02X}" for b in radio.mac_address)
                _log(f"    mac        : {mac}")
            except (AttributeError, TypeError):
                pass
            try:
                _log(f"    hostname   : {radio.hostname}")
            except AttributeError:
                pass
            try:
                _log(f"    tx_power   : {radio.tx_power} dBm")
            except (AttributeError, NotImplementedError):
                pass
            try:
                ap = radio.ap_info
                if ap:
                    _log(f"    ssid       : {ap.ssid}")
                    _log(f"    rssi       : {ap.rssi} dBm  ch={ap.channel}")
            except (AttributeError, NotImplementedError):
                pass
        else:
            _log("    not connected")
    except ImportError:
        _log("  Network: wifi module unavailable")
    except Exception as exc:  # noqa: BLE001
        _log(f"  Network info unavailable ({exc})")


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
# NTP time sync
# ---------------------------------------------------------------------------


def _check_ntp(rtc) -> bool:
    _section("NTP Time Sync")
    if rtc is None:
        _result("NTP sync", False, "skipped — RTC not available")
        return False
    try:
        from featherweather.rtc.rtc_sync import sync_rtc_from_ntp  # noqa: PLC0415
        ok = sync_rtc_from_ntp(rtc, tz_offset=_NTP_TZ_OFFSET)
        _result("NTP sync", ok, "" if ok else "check WiFi credentials in settings.toml")
        return ok
    except Exception as exc:  # noqa: BLE001
        _result("NTP sync", False, str(exc))
        return False


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
        lat = gps.latitude  if gps.latitude  is not None else 0.0
        lon = gps.longitude if gps.longitude is not None else 0.0
        alt = gps.altitude_m if gps.altitude_m is not None else 0.0
        _result(
            "GPS fix",
            True,
            (
                f"lat={lat:.6f}  lon={lon:.6f}"
                f"  alt={alt:.1f}m  sats={gps.satellites}  utc={ts}"
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

# SD card — mounted here in code.py's VM (boot.py mount is torn down before
# code.py starts; see boot.py for details).
sd_ok = _verify_sd()
_track(sd_ok)

# RTC provides the timestamp used in the report filename
rtc = _check_rtc(i2c)
_track(rtc is not None)

# Sync RTC from NTP — fixes unset / drifted time before the filename is stamped
ntp_ok = _check_ntp(rtc)
_track(ntp_ok)

# Enumerate all I2C addresses before probing individual sensors
i2c_addrs = _scan_i2c(i2c)

# Individual sensor checks
baro_data = _check_bmp390(i2c)
_track(baro_data is not None)

th_data = _check_shtc3(i2c)
_track(th_data is not None)

aq_data = _check_hm3301(i2c)
_track(aq_data is not None)

# GPS is on UART, not I2C
gps, gps_has_fix = _check_gps()
gps_alive = gps is not None
_track(gps_alive)

# Disk usage
_check_disk()

# System resources (htop-style snapshot — informational, not tracked)
_check_system()

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

_section("Diagnostic Summary")

_result("SD card",             sd_ok)
_result("PCF8523 RTC",         rtc is not None)
_result("NTP sync",            ntp_ok)
_result("BMP390 Barometric",   baro_data is not None)
_result("SHTC3 Temp/Humidity", th_data is not None)
_result("HM3301 Air Quality",  aq_data is not None)

if gps_has_fix:
    _result("GPS", True, "fix acquired")
elif gps_alive:
    _result("GPS module", True, "NMEA active — no fix yet (normal indoors/cold start)")
else:
    _result("GPS", False, "no response")

total_tests = _pass_count + _fail_count
all_ok = _fail_count == 0
fail_pct = (_fail_count / total_tests * 100) if total_tests else 0.0

_log()
_log(f"Tests: {total_tests}  passed={_pass_count}  failed={_fail_count}")
_log("Overall: " + ("ALL SYSTEMS GO" if all_ok else "ISSUES DETECTED — see details above"))
_log()

# NeoPixel summary flash:
#   All passed  → green × total_tests
#   < 50% failed → yellow × failed_count
#   ≥ 50% failed → red × failed_count
if all_ok:
    _flash(_GREEN, count=total_tests, on_ms=300, off_ms=150)
elif fail_pct < 50.0:
    _flash(_YELLOW, count=_fail_count, on_ms=300, off_ms=150)
else:
    _flash(_RED, count=_fail_count, on_ms=300, off_ms=150)

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
