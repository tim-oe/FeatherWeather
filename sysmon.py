"""FeatherWeather System Monitor — htop-style live resource view.

Deploy as code.py to watch live resource usage on the device:

    poetry run deploy --serial --sysmon
    poetry run mpremote connect /dev/ttyACM0 repl
    # Ctrl+D to soft-reboot and let it run automatically; Ctrl+X to exit REPL

Why no real htop?
  CircuitPython on ESP32 doesn't expose the FreeRTOS task table or
  per-core CPU counters from the underlying ESP-IDF
  (https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/system/index.html),
  so we report what *is* exposed: heap, CPU clock/temp/voltage, WiFi
  state, uptime, and a gc.collect() timing as a load proxy.

Output (refreshes every REFRESH_S seconds):

  uptime    cpu_mhz  temp_c  volt   heap_used  heap_free  use%   gc_ms  rssi  ip
  -------   -------  ------  -----  ---------  ---------  -----  -----  ----  ---------------
       12      240    47.1   3.24       42 KB     156 KB    21%    1.2   -52  192.168.1.25
       13      240    47.3   3.24       43 KB     155 KB    22%    1.3   -52  192.168.1.25
"""

import gc
import time

import microcontroller
import supervisor

REFRESH_S: float = 1.0
HEADER_EVERY: int = 20      # reprint header every N rows


def _safe(getter, default="-"):
    """Call getter() and return its result; on failure or None return default.

    Some CircuitPython builds return None for unsupported microcontroller
    properties (e.g. cpu.voltage on Feather ESP32 V2) instead of raising.
    """
    try:
        result = getter()
        if result is None:
            return default
        return result
    except Exception:  # noqa: BLE001
        return default


def _wifi_status() -> tuple:
    """Return (rssi_str, ip_str) for the currently associated WiFi network."""
    try:
        import wifi  # noqa: PLC0415
        radio = wifi.radio
        if not radio.connected:
            return "-", "disconnected"
        ip = str(radio.ipv4_address) if radio.ipv4_address else "-"
        rssi = "-"
        try:
            ap = radio.ap_info
            if ap:
                rssi = str(ap.rssi)
        except (AttributeError, NotImplementedError):
            pass
        return rssi, ip
    except Exception:  # noqa: BLE001
        return "-", "n/a"


def _fmt_num(val, spec: str, default: str = "-") -> str:
    """Format val using spec, returning default if val is None or unformattable."""
    if val is None:
        return default
    try:
        return f"{val:{spec}}"
    except (TypeError, ValueError):
        return default


def _snapshot() -> dict:
    """One full system snapshot — fields are htop-style strings ready to print."""
    t0 = time.monotonic_ns()
    gc.collect()
    gc_ms = (time.monotonic_ns() - t0) / 1_000_000

    free = gc.mem_free()
    alloc = gc.mem_alloc()
    total = free + alloc
    pct = (alloc / total * 100) if total else 0.0

    cpu = microcontroller.cpu
    freq_hz = _safe(lambda: cpu.frequency, default=None)
    temp_c  = _safe(lambda: cpu.temperature, default=None)
    volts   = _safe(lambda: cpu.voltage, default=None)
    rssi, ip = _wifi_status()

    return {
        "uptime": int(time.monotonic()),
        "cpu_mhz": int(freq_hz / 1_000_000) if freq_hz else "-",
        "temp_c": _fmt_num(temp_c, ".1f"),
        "volt":   _fmt_num(volts,  ".2f"),
        "heap_used_kb": alloc // 1024,
        "heap_free_kb": free // 1024,
        "heap_pct": int(pct),
        "gc_ms": gc_ms,
        "rssi": rssi,
        "ip": ip,
    }


_HEADER = (
    f"{'uptime':>7}  {'cpu_mhz':>7}  {'temp_c':>6}  {'volt':>5}  "
    f"{'heap_used':>9}  {'heap_free':>9}  {'use%':>4}  {'gc_ms':>5}  "
    f"{'rssi':>4}  ip"
)
_DIVIDER = (
    f"{'-'*7}  {'-'*7}  {'-'*6}  {'-'*5}  "
    f"{'-'*9}  {'-'*9}  {'-'*4}  {'-'*5}  "
    f"{'-'*4}  {'-'*15}"
)


def _format_row(s: dict) -> str:
    return (
        f"{s['uptime']:>7}  {s['cpu_mhz']:>7}  {s['temp_c']:>6}  {s['volt']:>5}  "
        f"{s['heap_used_kb']:>6} KB  {s['heap_free_kb']:>6} KB  {s['heap_pct']:>3}%  "
        f"{s['gc_ms']:>5.1f}  {s['rssi']:>4}  {s['ip']}"
    )


def _print_banner() -> None:
    try:
        import os  # noqa: PLC0415
        u = os.uname()
        ver = u.release
    except Exception:  # noqa: BLE001
        ver = "?"

    boot_reason = _safe(lambda: str(supervisor.runtime.run_reason), "?")
    reset_reason = _safe(lambda: str(microcontroller.cpu.reset_reason), "?")

    print("=" * 80)
    print(f"FeatherWeather System Monitor   CircuitPython {ver}")
    print(f"reset_reason={reset_reason}   run_reason={boot_reason}")
    print(f"refresh={REFRESH_S}s   press Ctrl+C to exit, Ctrl+D to soft-reboot")
    print("=" * 80)


def main() -> None:
    _print_banner()
    print(_HEADER)
    print(_DIVIDER)
    row = 0
    while True:
        try:
            print(_format_row(_snapshot()))
        except Exception as exc:  # noqa: BLE001
            print(f"snapshot failed: {exc}")
        row += 1
        if row % HEADER_EVERY == 0:
            print(_HEADER)
            print(_DIVIDER)
        time.sleep(REFRESH_S)


main()
