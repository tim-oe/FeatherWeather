"""NTP → PCF8523 RTC sync for CircuitPython.

Fetches current time from the network NTP pool and writes it to the
hardware RTC on the Adalogger FeatherWing.  WiFi must already be
configured in settings.toml (CIRCUITPY_WIFI_SSID / CIRCUITPY_WIFI_PASSWORD);
CircuitPython connects automatically at boot before code.py runs.

Usage:
    from featherweather.rtc.rtc_sync import sync_rtc_from_ntp

    synced = sync_rtc_from_ntp(rtc, tz_offset=-6)
    if not synced:
        print("Running on stale RTC time")

Optional settings.toml knobs:
    NTP_TIMEZONE_OFFSET  standard (non-DST) hours from UTC (e.g. -6 for CST)
    NTP_DST              "US" to auto-apply US DST rules (default), "none" to disable
    NTP_SERVERS          comma-separated list of NTP servers to try in order
                         (default: "pool.ntp.org,time.google.com,time.cloudflare.com")
    NTP_SOCKET_TIMEOUT   per-server UDP socket timeout in seconds (default: 10)
"""

import os
import time

__all__ = ["get_rtc", "sync_rtc_from_ntp", "PCF8523_I2C_ADDR", "PCF8523_LABEL"]

PCF8523_I2C_ADDR: int = 0x68
PCF8523_LABEL: str = "PCF8523 (Adalogger RTC)"

_DEFAULT_SERVERS = "pool.ntp.org,time.google.com,time.cloudflare.com"


def get_rtc():
    """Create and return a PCF8523 RTC on the shared I2C bus.

    The PCF8523 lives on the Adalogger FeatherWing (I2C address 0x68).
    Uses the shared bus from ``featherweather.hardware.i2c_bus`` so no
    separate I2C object is needed in code.py or diagnostic.py.

    Returns:
        adafruit_pcf8523.pcf8523.PCF8523 instance ready for use.
    """
    import adafruit_pcf8523.pcf8523 as _pcf8523_mod  # noqa: PLC0415

    from featherweather.hardware.i2c_bus import get_i2c  # noqa: PLC0415

    return _pcf8523_mod.PCF8523(get_i2c())
_WIFI_WAIT_S = 15.0
_POLL_INTERVAL_S = 0.5

# Standard (non-DST) UTC offsets for common US/world timezones.
# DST (+1h) is applied automatically by _us_dst_offset() when NTP_DST=US.
_TZ_OFFSETS = {
    # US
    "america/new_york": -5,
    "america/chicago": -6,
    "america/denver": -7,
    "america/phoenix": -7,  # Arizona — no DST
    "america/los_angeles": -8,
    "america/anchorage": -9,
    "pacific/honolulu": -10,  # Hawaii — no DST
    # Short aliases
    "est": -5,
    "cst": -6,
    "mst": -7,
    "pst": -8,
    "edt": -5,
    "cdt": -6,
    "mdt": -7,
    "pdt": -8,
    "et": -5,
    "ct": -6,
    "mt": -7,
    "pt": -8,
    # UTC
    "utc": 0,
    "gmt": 0,
}

# Timezones that never observe DST (stays on standard time all year)
_NO_DST = {"america/phoenix", "pacific/honolulu"}


def _resolve_tz():
    """Return (std_offset, use_dst) from NTP_TIMEZONE or NTP_TIMEZONE_OFFSET."""
    tz_name = (os.getenv("NTP_TIMEZONE") or "").strip().lower()
    if tz_name:
        offset = _TZ_OFFSETS.get(tz_name)
        if offset is None:
            print(f"[NTP] unknown NTP_TIMEZONE '{tz_name}', defaulting to UTC")
            return 0, False
        use_dst = tz_name not in _NO_DST
        return offset, use_dst

    # Fall back to raw numeric offset
    raw = os.getenv("NTP_TIMEZONE_OFFSET")
    try:
        return int(raw) if raw else 0, True
    except (TypeError, ValueError):
        return 0, True


def _ntp_servers():
    raw = os.getenv("NTP_SERVERS") or _DEFAULT_SERVERS
    return [s.strip() for s in raw.split(",") if s.strip()]


def _socket_timeout():
    raw = os.getenv("NTP_SOCKET_TIMEOUT")
    try:
        return int(raw) if raw else 10
    except (TypeError, ValueError):
        return 10


def _weekday(year, month, day):
    """Return 0=Monday … 6=Sunday (Tomohiko Sakamoto algorithm — no mktime needed)."""
    _t = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4]
    if month < 3:
        year -= 1
    return (year + year // 4 - year // 100 + year // 400 + _t[month - 1] + day) % 7


def _nth_sunday(year, month, n):
    """Return the day-of-month for the nth Sunday (1-based) in the given month."""
    wday = _weekday(year, month, 1)  # 0=Mon … 6=Sun
    days_to_first_sunday = (6 - wday) % 7
    return 1 + days_to_first_sunday + (n - 1) * 7


def _us_dst_offset(utc_struct, std_offset):
    """Return 1 if US DST is in effect for the given UTC time, else 0.

    US rules: DST starts 2nd Sunday of March at 02:00 local standard time,
    ends 1st Sunday of November at 02:00 local standard time (= 01:00 DST).
    """
    year = utc_struct.tm_year
    month = utc_struct.tm_mon
    day = utc_struct.tm_mday
    hour = utc_struct.tm_hour

    if month < 3 or month > 11:
        return 0
    if 4 <= month <= 10:
        return 1

    start_day = _nth_sunday(year, 3, 2)  # 2nd Sunday of March
    end_day = _nth_sunday(year, 11, 1)  # 1st Sunday of November

    # DST starts at 02:00 local standard = 02:00 - std_offset UTC
    start_utc_hour = 2 - std_offset
    # DST ends at 02:00 local standard = same UTC hour (clocks fall back)
    end_utc_hour = 2 - std_offset

    if month == 3:
        if day > start_day:
            return 1
        if day == start_day and hour >= start_utc_hour:
            return 1
        return 0

    # month == 11
    if day < end_day:
        return 1
    if day == end_day and hour < end_utc_hour:
        return 1
    return 0


def sync_rtc_from_ntp(rtc, tz_offset=0):
    """Fetch time from NTP and set the PCF8523 RTC.

    Tries each server in NTP_SERVERS in order; returns True on the first
    successful sync, False only if every server times out / errors.

    DST is applied automatically when NTP_DST="US" (the default).  Set
    NTP_TIMEZONE_OFFSET to your standard (non-DST) offset, e.g. -6 for
    CST/CDT — no need to change it twice a year.

    Args:
        rtc:       PCF8523 instance (already initialised on the I2C bus)
        tz_offset: standard (non-DST) timezone offset in whole hours from UTC

    Returns:
        True if the RTC was updated successfully, False on any failure.
    """
    try:
        import adafruit_ntp  # noqa: PLC0415
        import socketpool  # noqa: PLC0415 — CircuitPython built-in
        import wifi  # noqa: PLC0415 — CircuitPython built-in
    except ImportError as exc:
        print(f"[NTP] import failed: {exc}")
        return False

    # Wait for the automatic WiFi connection that CircuitPython establishes
    # before code.py runs (driven by CIRCUITPY_WIFI_SSID in settings.toml).
    deadline = time.monotonic() + _WIFI_WAIT_S
    while not wifi.radio.connected and time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL_S)

    if not wifi.radio.connected:
        print("[NTP] sync skipped — no WiFi connection")
        return False

    print(
        f"[NTP] WiFi ip={wifi.radio.ipv4_address} "
        f"gw={wifi.radio.ipv4_gateway} dns={wifi.radio.ipv4_dns}"
    )

    pool = socketpool.SocketPool(wifi.radio)
    timeout = _socket_timeout()

    for server in _ntp_servers():
        try:
            # Fetch raw UTC from NTP (tz_offset=0) so we can compute DST first
            ntp = adafruit_ntp.NTP(
                pool,
                server=server,
                tz_offset=0,
                socket_timeout=timeout,
                cache_seconds=3600,
            )
            utc_time = ntp.datetime

            # Resolve timezone — prefers NTP_TIMEZONE name, falls back to NTP_TIMEZONE_OFFSET
            std_offset, auto_dst = _resolve_tz()
            dst_mode = (os.getenv("NTP_DST") or "US").upper()
            dst_hours = (
                _us_dst_offset(utc_time, std_offset)
                if (auto_dst and dst_mode == "US")
                else 0
            )
            total_offset = std_offset + dst_hours

            # Re-fetch with the final offset so the struct_time is correct
            ntp2 = adafruit_ntp.NTP(
                pool,
                server=server,
                tz_offset=total_offset,
                socket_timeout=timeout,
                cache_seconds=3600,
            )
            ntp_time = ntp2.datetime
            rtc.datetime = ntp_time

            # Also update CircuitPython's internal system clock so FAT file
            # timestamps match wall-clock time from the first write onwards.
            import rtc as _cp_rtc  # noqa: PLC0415

            _cp_rtc.RTC().datetime = ntp_time

            dst_label = f"+{dst_hours}h DST" if dst_hours else "no DST"
            dt = rtc.datetime
            print(
                f"[NTP] RTC synced via {server} → "
                f"{dt.tm_year}-{dt.tm_mon:02d}-{dt.tm_mday:02d} "
                f"{dt.tm_hour:02d}:{dt.tm_min:02d}:{dt.tm_sec:02d} "
                f"(UTC{total_offset:+d}, {dst_label})"
            )
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[NTP] {server} failed: {exc}")

    print("[NTP] sync failed — all servers exhausted")
    return False
