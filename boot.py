"""boot.py — runs once before code.py on every boot.

Responsibilities
----------------
1. Mount the Adalogger SD card at /sd for data storage.

2. Seed CircuitPython's internal clock from the PCF8523 RTC so that FAT
   file timestamps are correct from the first write.

Pin assignments (ESP32 Feather V2):
    SPI  SCK/MOSI/MISO   board.SCK/MOSI/MISO
    SPI  CS              board.D33
    I2C  SCL/SDA         board.SCL/board.SDA
"""

import board
import busio
import digitalio
import storage
import adafruit_sdcard
import rtc as _cp_rtc
import adafruit_pcf8523.pcf8523 as _pcf8523

_SD_CS_PIN = board.D33


# ---------------------------------------------------------------------------
# SD card
# ---------------------------------------------------------------------------

def _mount_sd() -> None:
    """Mount the SD card at /sd."""
    try:
        spi    = busio.SPI(board.SCK, board.MOSI, board.MISO)
        cs     = digitalio.DigitalInOut(_SD_CS_PIN)
        sdcard = adafruit_sdcard.SDCard(spi, cs)
        vfs    = storage.VfsFat(sdcard)
        storage.mount(vfs, "/sd")
        print("boot: SD card mounted at /sd")
    except Exception as exc:  # noqa: BLE001
        print(f"boot: SD card mount failed: {exc}")


# ---------------------------------------------------------------------------
# RTC → system clock
# ---------------------------------------------------------------------------

def _sync_system_clock() -> None:
    """Read PCF8523 and set CircuitPython's internal clock so FAT timestamps are correct."""
    try:
        i2c    = busio.I2C(board.SCL, board.SDA)
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


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

_mount_sd()
_sync_system_clock()
