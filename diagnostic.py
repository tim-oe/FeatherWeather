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
    SEN0575      tipping bucket rainfall            I2C 0x1D  (DIP → I2C mode)
    PCF8523      Adalogger FeatherWing RTC          I2C 0x68
    SH1107       OLED FeatherWing #4650 128×64      I2C 0x3C
    SEN0482      wind direction                     RS485 Modbus (default addr 0x02)
    SEN0483      wind speed                         RS485 Modbus (default addr 0x03)
    SEN0644      illuminance                        RS485 Modbus (default addr 0x01)
    SPH0645      I2S MEMS microphone #3421          I2S (D27/D13/A2)
    Ultimate GPS UART NMEA activity + optional fix  board.TX / board.RX
    SD card      Adalogger FeatherWing SPI storage  board.D33 CS

Output
------
    Serial console              live progress with [PASS] / [FAIL] per device
    NeoPixel                    green flash = pass, red flash = fail per test;
                                summary flash: green×N (all pass), yellow×N
                                (<50% fail), red×N (≥50% fail)
    /sd/diag_YYYYMMDD_HHMMSS.txt  persisted report (diag_unknown.txt if RTC fails)
"""

import time

from featherweather.display.display_controller import DisplayController
from featherweather.display.neopixel_indicator import NeoPixelIndicator
from featherweather.gps.gps_reader import GpsReader
from featherweather.hardware.i2c_bus import get_i2c
from featherweather.hardware.rs485_diagnostic import Rs485Diagnostic
from featherweather.hardware.sd_card import verify_sd
from featherweather.rtc.rtc_sync import PCF8523_I2C_ADDR, PCF8523_LABEL, get_rtc, sync_rtc_from_ntp
from featherweather.sensors.air_quality.air_quality_reader import AirQualityReader
from featherweather.sensors.barometric.barometric_reader import BarometricReader
from featherweather.sensors.illuminance.illuminance_reader import IlluminanceReader
from featherweather.sensors.microphone.microphone_reader import MicrophoneReader
from featherweather.sensors.rainfall.rainfall_reader import RainfallReader
from featherweather.sensors.temp_humidity.temp_humidity_reader import TempHumidityReader
from featherweather.sensors.wind_direction.wind_direction_reader import WindDirectionReader
from featherweather.sensors.wind_speed.wind_speed_reader import WindSpeedReader

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_GPS_NMEA_CHECK_S: float = 5.0    # seconds to wait for the first NMEA sentence
_GPS_FIX_TIMEOUT_S: float = 30.0  # seconds to attempt a GPS fix
_SHUTDOWN_LINGER_S: float = 3.0   # seconds to keep OLED summary on screen
_DEBOUNCE_S: float = 0.08         # button debounce window in seconds

# Built from each class's own _I2C_ADDR / _LABEL — never edit this dict directly.
# To add a new device: set _I2C_ADDR and _LABEL on its class, then add the class here.
_KNOWN_I2C_ADDRS: dict = {
    cls._I2C_ADDR: cls._LABEL
    for cls in (BarometricReader, TempHumidityReader, AirQualityReader, RainfallReader)
}
_KNOWN_I2C_ADDRS[DisplayController.I2C_ADDR] = DisplayController.LABEL
_KNOWN_I2C_ADDRS[PCF8523_I2C_ADDR] = PCF8523_LABEL

# ---------------------------------------------------------------------------
# NeoPixel — single built-in pixel for visual pass/fail feedback
# ---------------------------------------------------------------------------

_pixel = NeoPixelIndicator()

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

_pass_count: int = 0
_fail_count: int = 0


def _track(ok: bool) -> None:
    """Record a top-level test result and flash the NeoPixel accordingly."""
    global _pass_count, _fail_count
    if ok:
        _pass_count += 1
        _pixel.flash_pass()
    else:
        _fail_count += 1
        _pixel.flash_fail()

# ---------------------------------------------------------------------------
# Report buffer
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


def _result(lbl: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    _log(f"  [{status}] {lbl}{suffix}")


# ---------------------------------------------------------------------------
# SD card — mount and verify
# ---------------------------------------------------------------------------


def _check_sd() -> bool:
    """Mount the SD card and confirm it is real and writable."""
    _section("Adalogger SD Card")
    ok, detail, entries = verify_sd()
    if not ok:
        _result("SD card", False, detail)
        return False
    _result("SD card mount", True, detail)
    _result("SD card write", True)
    _log(f"    /sd contents: {entries}")
    return True


# ---------------------------------------------------------------------------
# Disk metrics — os.statvfs() equivalent of df -h
# ---------------------------------------------------------------------------


def _check_disk(sd_available: bool) -> None:
    """Report filesystem usage for / and /sd."""
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

    _log("  SD card  /sd")
    if not sd_available:
        _log("    unavailable — SD card not accessible (see SD card section above)")
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
            _log(f"    used   : {_fmt_bytes(used_bytes)}  (total unavailable)")
        _log(f"    files  : {file_count}  dirs : {dir_count}")
    except Exception as exc:  # noqa: BLE001
        _log(f"    unavailable ({exc})")


# ---------------------------------------------------------------------------
# System resources
# ---------------------------------------------------------------------------


def _check_system() -> None:
    """One-shot snapshot of CPU, memory, runtime, network, firmware."""
    _section("System Resources")
    import gc  # noqa: PLC0415
    import os  # noqa: PLC0415

    try:
        u = os.uname()
        _log("  Firmware")
        _log(f"    sysname    : {u.sysname}")
        _log(f"    release    : {u.release}")
        _log(f"    version    : {u.version}")
        _log(f"    machine    : {u.machine}")
    except Exception as exc:  # noqa: BLE001
        _log(f"  Firmware info unavailable ({exc})")

    def _fmt(val, spec: str, suffix: str = "") -> str:
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
# PCF8523 RTC
# ---------------------------------------------------------------------------


def _check_rtc():
    _section("PCF8523 RTC (Adalogger FeatherWing)")
    try:
        rtc = get_rtc()
        dt = rtc.datetime
        ts = (
            f"{dt.tm_year}-{dt.tm_mon:02d}-{dt.tm_mday:02d}"
            f" {dt.tm_hour:02d}:{dt.tm_min:02d}:{dt.tm_sec:02d}"
        )
        _result("PCF8523", True, ts)
        return rtc
    except Exception as exc:  # noqa: BLE001
        _result("PCF8523", False, str(exc))
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
        ok = sync_rtc_from_ntp(rtc)
        if ok:
            _result("NTP sync", True, "")
            return True
        try:
            import wifi  # noqa: PLC0415
            if not wifi.radio.connected:
                hint = "WiFi not connected — check CIRCUITPY_WIFI_SSID/PASSWORD"
            else:
                hint = (
                    f"WiFi up (ip={wifi.radio.ipv4_address}); "
                    "router/ISP likely blocking UDP/123"
                )
        except Exception:  # noqa: BLE001
            hint = "WiFi state unavailable"
        _result("NTP sync", False, hint)
        return False
    except Exception as exc:  # noqa: BLE001
        _result("NTP sync", False, str(exc))
        return False


# ---------------------------------------------------------------------------
# I2C bus scan
# ---------------------------------------------------------------------------


def _scan_i2c() -> list:
    _section("I2C Bus Scan")
    i2c = get_i2c()
    while not i2c.try_lock():
        pass
    try:
        found = i2c.scan()
    finally:
        i2c.unlock()

    if found:
        _log(f"  Found {len(found)} device(s): {[hex(a) for a in found]}")
        for addr in found:
            name = _KNOWN_I2C_ADDRS.get(addr, "Unknown device")
            _log(f"    {hex(addr):<6}  {name}")
    else:
        _log("  No I2C devices found — check wiring and bus frequency!")
    return found


# ---------------------------------------------------------------------------
# Sensor checks — each delegates all sensor logic to the reader class
# ---------------------------------------------------------------------------


def _check_bmp390():
    _section("BMP390 Barometric Sensor")
    try:
        data = BarometricReader.verify()
        _result("BMP390", True, f"pressure={data.pressure:.2f} hPa  temp={data.temperature:.2f} C")
        return data
    except Exception as exc:  # noqa: BLE001
        _result("BMP390", False, str(exc))
        return None


def _check_shtc3():
    _section("SHTC3 Temperature / Humidity Sensor")
    try:
        data = TempHumidityReader.verify()
        _result("SHTC3", True, f"temp={data.temperature:.2f} C  hum={data.relative_humidity:.1f} %RH")
        return data
    except Exception as exc:  # noqa: BLE001
        _result("SHTC3", False, str(exc))
        return None


def _check_hm3301():
    _section("HM3301 Air Quality Sensor")
    _log(f"  Waiting {AirQualityReader._VERIFY_WARMUP_S:.0f}s for warmup …")
    try:
        data = AirQualityReader.verify()
        _result("HM3301", True,
                f"PM1.0={data.pm_1_0_atm}  PM2.5={data.pm_2_5_atm}  PM10={data.pm_10_atm} ug/m3")
        return data
    except Exception as exc:  # noqa: BLE001
        _result("HM3301", False, str(exc))
        return None


def _check_sen0575():
    _section("SEN0575 Tipping Bucket Rainfall Sensor")
    _log("  DIP switch must be set to I2C mode (not UART).")
    try:
        data = RainfallReader.verify()
        _result(
            "SEN0575",
            True,
            f"cumulative={data.cumulative_rainfall_mm:.2f} mm"
            f"  tips={data.bucket_count}"
            f"  uptime={data.working_time_h:.1f} h"
            f"  1h={data.rainfall_window_mm:.2f} mm",
        )
        return data
    except Exception as exc:  # noqa: BLE001
        _result("SEN0575", False, str(exc))
        return None


def _check_rs485_bus() -> None:
    """Log RS485 UART config; does not count as a pass/fail test."""
    _section("RS485 Bus (DFR0845 adapter)")
    try:
        Rs485Diagnostic(log=_log).log_bus_config()
    except Exception as exc:  # noqa: BLE001
        _log(f"  RS485 bus init failed: {exc}")


def _check_wind_direction():
    _section("SEN0482 Wind Direction Sensor (RS485)")
    try:
        data = WindDirectionReader.verify()
        _result("SEN0482", True, f"{data.degrees:.1f}°  {data.direction_label}  code={data.direction_code}")
        return data
    except Exception as exc:  # noqa: BLE001
        _result("SEN0482", False, str(exc))
        return None


def _check_wind_speed():
    _section("SEN0483 Wind Speed Sensor (RS485)")
    try:
        data = WindSpeedReader.verify()
        _result("SEN0483", True, f"{data.speed_ms:.1f} m/s  {data.beaufort}")
        return data
    except Exception as exc:  # noqa: BLE001
        _result("SEN0483", False, str(exc))
        return None


def _check_illuminance():
    _section("SEN0644 Illuminance Sensor (RS485)")
    try:
        data = IlluminanceReader.verify()
        _result("SEN0644", True, f"{data.lux:.1f} lux")
        return data
    except Exception as exc:  # noqa: BLE001
        _result("SEN0644", False, str(exc))
        return None


def _check_mic():
    _section("SPH0645 I2S MEMS Microphone #3421")
    try:
        data = MicrophoneReader.verify()
        _result("SPH0645", True, f"{data.db_spl:.1f} dB SPL  rms={data.rms:.1f}")
        return data, False
    except NotImplementedError as exc:
        _log(f"  [SKIP] {exc}")
        return None, True
    except Exception as exc:  # noqa: BLE001
        _result("SPH0645", False, str(exc))
        return None, False


# ---------------------------------------------------------------------------
# OLED check — verify the DisplayController is operational
# ---------------------------------------------------------------------------


def _check_oled(display: DisplayController) -> bool:
    """Render a diagnostic splash and confirm the display is working."""
    _section("OLED FeatherWing #4650 (SH1107 128x64)")
    try:
        display.display("FeatherWeather", "DIAG MODE", "I2C 0x3C  OK", "Running checks..")
        _result("SH1107", True, "I2C 0x3C, 128x64, rotation=270")
        return True
    except Exception as exc:  # noqa: BLE001
        _result("SH1107", False, str(exc))
        return False


# ---------------------------------------------------------------------------
# GPS check
# ---------------------------------------------------------------------------


def _check_gps():
    _section("Ultimate GPS FeatherWing")
    _log(f"  NMEA timeout={_GPS_NMEA_CHECK_S:.0f}s  fix timeout={_GPS_FIX_TIMEOUT_S:.0f}s")
    try:
        data = GpsReader.verify(
            nmea_timeout_s=_GPS_NMEA_CHECK_S,
            fix_timeout_s=_GPS_FIX_TIMEOUT_S,
        )
    except RuntimeError as exc:
        _result("GPS NMEA activity", False, str(exc))
        return None, False
    except Exception as exc:  # noqa: BLE001
        _result("GPS UART init", False, str(exc))
        return None, False

    _result("GPS NMEA activity", True)

    if data.has_fix:
        ts = (
            f"{data.timestamp_utc.tm_hour:02d}:{data.timestamp_utc.tm_min:02d}:"
            f"{data.timestamp_utc.tm_sec:02d}Z"
            if data.timestamp_utc else "n/a"
        )
        _result(
            "GPS fix",
            True,
            f"lat={data.latitude:.6f}  lon={data.longitude:.6f}"
            f"  alt={data.altitude_m:.1f}m  sats={data.satellites}  utc={ts}",
        )
    else:
        _result("GPS fix (no fix yet)", True, "module alive, needs clear sky view")

    return data, data.has_fix


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _write_report(rtc) -> None:
    if rtc is not None:
        dt = rtc.datetime
        stamped_sd = (
            f"/sd/diag_{dt.tm_year}{dt.tm_mon:02d}{dt.tm_mday:02d}"
            f"_{dt.tm_hour:02d}{dt.tm_min:02d}{dt.tm_sec:02d}.txt"
        )
    else:
        stamped_sd = None

    all_targets = ["/sd/diag_latest.txt"]
    if stamped_sd is not None:
        all_targets.append(stamped_sd)

    for filename in all_targets:
        try:
            with open(filename, "w") as f:
                for line in _report_lines:
                    safe = "".join(c if ord(c) < 128 else "?" for c in line)
                    f.write(safe + "\n")
                f.flush()
            print(f"Report saved -> {filename}")
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to write {filename}: {exc}")




# ---------------------------------------------------------------------------
# Done loop — hold OLED summary, toggle on button press
# ---------------------------------------------------------------------------


def _done_loop(display: DisplayController, passed: int, failed: int) -> None:
    """Hold the OLED summary, then let any button toggle it on/off.

    Displays a final pass/fail summary for _SHUTDOWN_LINGER_S seconds,
    then blanks the screen.  Any OLED button press toggles between the
    summary and blank until the device is reset.  The loop never returns.
    """
    total = passed + failed
    status = "ALL PASS" if failed == 0 else f"{failed}/{total} FAIL"
    summary = ("FeatherWeather", "DIAG COMPLETE", f"Pass: {passed}  {status}", f"Fail: {failed}")

    display.display(*summary)
    time.sleep(_SHUTDOWN_LINGER_S)
    display.blank()

    showing = False
    last_press = time.monotonic()
    while True:
        if display.any_button_pressed():
            now = time.monotonic()
            if (now - last_press) > _DEBOUNCE_S:
                last_press = now
                showing = not showing
                if showing:
                    display.display(*summary)
                else:
                    display.blank()
        time.sleep(0.05)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_log("FeatherWeather Diagnostic Mode")
_log(f"Started  monotonic={time.monotonic():.1f}s")
_log("Checking: BMP390, HM3301, SHTC3, PCF8523 RTC, SH1107 OLED, SEN0482/0483/0644 RS485, GPS, SD card")

# DisplayController calls displayio.release_displays() internally and acquires
# the shared I2C bus — must be created first.
try:
    _display = DisplayController()
    print("[init] DisplayController OK")
except Exception as _exc:  # noqa: BLE001
    _display = None
    print(f"[init] DisplayController FAILED: {_exc}")

# SD card
sd_ok = _check_sd()
_track(sd_ok)

# RTC provides the timestamp used in the report filename
rtc = _check_rtc()
_track(rtc is not None)

# Sync RTC from NTP
ntp_ok = _check_ntp(rtc)
_track(ntp_ok)

# Enumerate all I2C addresses before probing individual sensors
i2c_addrs = _scan_i2c()

# OLED — verify the DisplayController is functional
if _display is not None:
    oled_ok = _check_oled(_display)
else:
    _section("OLED FeatherWing #4650 (SH1107 128x64)")
    _result("SH1107", False, "DisplayController init failed")
    oled_ok = False
_track(oled_ok)

# Individual sensor checks
baro_data = _check_bmp390()
_track(baro_data is not None)

th_data = _check_shtc3()
_track(th_data is not None)

aq_data = _check_hm3301()
_track(aq_data is not None)

rain_data = _check_sen0575()
_track(rain_data is not None)

# RS485 Modbus sensors — share the DFR0845 bus on A0/A1
_check_rs485_bus()

wind_dir_data = _check_wind_direction()
_track(wind_dir_data is not None)

wind_spd_data = _check_wind_speed()
_track(wind_spd_data is not None)

illum_data = _check_illuminance()
_track(illum_data is not None)

mic_data, mic_skipped = _check_mic()
if not mic_skipped:
    _track(mic_data is not None)

# GPS is on UART, not I2C
gps, gps_has_fix = _check_gps()
gps_alive = gps is not None
_track(gps_alive)

# Disk usage and system info (informational)
_check_disk(sd_ok)
_check_system()

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

_section("Diagnostic Summary")

_result("SD card",             sd_ok)
_result("PCF8523 RTC",         rtc is not None)
_result("NTP sync",            ntp_ok)
_result("SH1107 OLED",         oled_ok)
_result("BMP390 Barometric",   baro_data is not None)
_result("SHTC3 Temp/Humidity", th_data is not None)
_result("HM3301 Air Quality",  aq_data is not None)
_result("SEN0575 Rainfall",    rain_data is not None)
_result("SEN0482 Wind Dir",    wind_dir_data is not None)
_result("SEN0483 Wind Speed",  wind_spd_data is not None)
_result("SEN0644 Illuminance", illum_data is not None)

if mic_skipped:
    _log("[SKIP] SPH0645 Microphone — needs audioi2sin (10.3.0-dev nightly)")
else:
    _result("SPH0645 Microphone", mic_data is not None)

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

_pixel.flash_summary(_pass_count, _fail_count)

# Update OLED with final pass/fail counts
if _display is not None:
    _display.display(
        "FeatherWeather",
        "DIAG COMPLETE",
        f"Pass: {_pass_count}",
        f"Fail: {_fail_count}",
    )

# ---------------------------------------------------------------------------
# Persist report to SD card
# ---------------------------------------------------------------------------

if sd_ok:
    _write_report(rtc)
else:
    _log("SD card unavailable — report not saved to disk")

_log("Diagnostic complete.")

_pixel.deinit()

# Hold the OLED summary and respond to button presses until reset.
# This loop never returns — exiting would drop CircuitPython to the REPL.
if _display is not None:
    _done_loop(_display, _pass_count, _fail_count)
else:
    while True:
        time.sleep(1)
