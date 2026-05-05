"""boot.py — runs once before code.py on every boot.

Mounts the Adalogger FeatherWing SD card at /sd so it is available for
the entire session (both diagnostic.py and code.py).  Mounting here
rather than inside code.py means the card stays accessible even after
code.py finishes and the device drops to the REPL.

Pin assignments (ESP32 Feather V2):
    SPI  SCK / MOSI / MISO   board.SCK / MOSI / MISO
    SPI  CS                  board.D33  (Feather "D10" slot → GPIO33)
"""

import board
import busio
import digitalio
import adafruit_sdcard
import storage

_SD_CS_PIN = board.D33


def _mount_sd() -> bool:
    try:
        spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
        cs = digitalio.DigitalInOut(_SD_CS_PIN)
        sdcard = adafruit_sdcard.SDCard(spi, cs)
        vfs = storage.VfsFat(sdcard)
        storage.mount(vfs, "/sd")
        print("boot: SD card mounted at /sd")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"boot: SD card mount failed: {exc}")
        return False


_mount_sd()
