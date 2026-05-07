"""boot.py — runs once before code.py on every boot.

Seeds CircuitPython's internal system clock from the PCF8523 RTC so that
FAT file timestamps are correct from the first write.

SD card mounting has intentionally been moved to code.py / diagnostic.py.
boot.py and code.py run in separate, consecutive Python VMs; any
storage.mount() call here is torn down before code.py starts, so the SD
card must be mounted by user code itself.
"""

import board
import busio
import rtc as _cp_rtc
import adafruit_pcf8523.pcf8523 as _pcf8523


def _sync_system_clock() -> None:
    """Read PCF8523 and set CircuitPython's internal clock so FAT timestamps are correct."""
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        hw_rtc = _pcf8523.PCF8523(i2c)
        _cp_rtc.RTC().datetime = hw_rtc.datetime
        dt = hw_rtc.datetime
        print(
            f"boot: system clock set to "
            f"{dt.tm_year}-{dt.tm_mon:02d}-{dt.tm_mday:02d} "
            f"{dt.tm_hour:02d}:{dt.tm_min:02d}:{dt.tm_sec:02d}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"boot: system clock sync failed: {exc}")


_sync_system_clock()
