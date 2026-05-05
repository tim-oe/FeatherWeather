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

NTP_TIMEZONE_OFFSET in settings.toml is passed in as tz_offset so the
RTC stores local wall-clock time rather than UTC.
"""

import time

__all__ = ["sync_rtc_from_ntp"]

_NTP_SERVER: str = "pool.ntp.org"
_WIFI_WAIT_S: float = 15.0   # max seconds to wait for WiFi at boot
_POLL_INTERVAL_S: float = 0.5


def sync_rtc_from_ntp(rtc, tz_offset: int = 0) -> bool:
    """Fetch time from NTP and set the PCF8523 RTC.

    Args:
        rtc:       PCF8523 instance (already initialised on the I2C bus)
        tz_offset: local timezone offset in whole hours from UTC
                   (e.g. -6 for CST, -5 for CDT, 0 for UTC)

    Returns:
        True if the RTC was updated successfully, False on any failure
        (no WiFi, DNS failure, socket error, etc.).
    """
    try:
        import wifi          # noqa: PLC0415 — CircuitPython built-in
        import socketpool    # noqa: PLC0415 — CircuitPython built-in
        import adafruit_ntp  # noqa: PLC0415
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

    try:
        pool = socketpool.SocketPool(wifi.radio)
        ntp = adafruit_ntp.NTP(
            pool,
            server=_NTP_SERVER,
            tz_offset=tz_offset,
            cache_seconds=3600,
        )
        rtc.datetime = ntp.datetime
        dt = rtc.datetime
        print(
            f"[NTP] RTC synced → "
            f"{dt.tm_year}-{dt.tm_mon:02d}-{dt.tm_mday:02d} "
            f"{dt.tm_hour:02d}:{dt.tm_min:02d}:{dt.tm_sec:02d} "
            f"(UTC{tz_offset:+d})"
        )
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[NTP] sync failed: {exc}")
        return False
