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
    NTP_TIMEZONE_OFFSET  hours from UTC, passed in as tz_offset (e.g. -6)
    NTP_SERVERS          comma-separated list of NTP servers to try in order
                         (default: "pool.ntp.org,time.google.com,time.cloudflare.com")
    NTP_SOCKET_TIMEOUT   per-server UDP socket timeout in seconds (default: 10)
"""

import os
import time

__all__ = ["sync_rtc_from_ntp"]

_DEFAULT_SERVERS = "pool.ntp.org,time.google.com,time.cloudflare.com"
_WIFI_WAIT_S: float = 15.0
_POLL_INTERVAL_S: float = 0.5


def _ntp_servers() -> list[str]:
    raw = os.getenv("NTP_SERVERS") or _DEFAULT_SERVERS
    return [s.strip() for s in raw.split(",") if s.strip()]


def _socket_timeout() -> int:
    raw = os.getenv("NTP_SOCKET_TIMEOUT")
    try:
        return int(raw) if raw else 10
    except (TypeError, ValueError):
        return 10


def sync_rtc_from_ntp(rtc, tz_offset: int = 0) -> bool:
    """Fetch time from NTP and set the PCF8523 RTC.

    Tries each server in NTP_SERVERS in order; returns True on the first
    successful sync, False only if every server times out / errors.

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

    print(
        f"[NTP] WiFi ip={wifi.radio.ipv4_address} "
        f"gw={wifi.radio.ipv4_gateway} dns={wifi.radio.ipv4_dns}"
    )

    pool = socketpool.SocketPool(wifi.radio)
    timeout = _socket_timeout()

    for server in _ntp_servers():
        try:
            ntp = adafruit_ntp.NTP(
                pool,
                server=server,
                tz_offset=tz_offset,
                socket_timeout=timeout,
                cache_seconds=3600,
            )
            ntp_time = ntp.datetime
            rtc.datetime = ntp_time
            # Also update CircuitPython's internal system clock so FAT file
            # timestamps match wall-clock time from the first write onwards.
            import rtc as _cp_rtc  # noqa: PLC0415
            _cp_rtc.RTC().datetime = ntp_time
            dt = rtc.datetime
            print(
                f"[NTP] RTC synced via {server} → "
                f"{dt.tm_year}-{dt.tm_mon:02d}-{dt.tm_mday:02d} "
                f"{dt.tm_hour:02d}:{dt.tm_min:02d}:{dt.tm_sec:02d} "
                f"(UTC{tz_offset:+d})"
            )
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[NTP] {server} failed: {exc}")

    print("[NTP] sync failed — all servers exhausted")
    return False
