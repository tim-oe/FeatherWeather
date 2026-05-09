"""SD card mount / verification helpers for FeatherWeather.

Centralises all SPI, SDCard, and VfsFat logic so that code.py and
diagnostic.py never import busio, digitalio, adafruit_sdcard, or storage
directly.

Pin assignments (Adalogger FeatherWing):
    SPI SCK / MOSI / MISO   board.SCK / board.MOSI / board.MISO
    CS                       board.D33

Environment variable:
    SD_CS_PIN  board attribute name for the chip-select pin  (default "D33")
"""

import os

import adafruit_sdcard
import board
import busio
import digitalio
import storage

__all__ = ["mount_sd", "verify_sd"]

_MIN_REAL_SD_BYTES: int = 10 * 1024 * 1024  # < 10 MB → CIRCUITPY flash, not SD


def _cs_pin():
    pin_name = os.getenv("SD_CS_PIN") or "D33"
    return getattr(board, pin_name)


def _create_vfs():
    """Return a mounted-ready VfsFat for the SD card (raises on failure)."""
    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    cs = digitalio.DigitalInOut(_cs_pin())
    sdcard = adafruit_sdcard.SDCard(spi, cs)
    return storage.VfsFat(sdcard)


def mount_sd() -> bool:
    """Mount the SD card at /sd, removing any stale boot.py mount first.

    Returns:
        True if /sd is accessible after the call, False on any failure.
    """
    try:
        storage.umount("/sd")
        print("SD card: removed stale /sd mount")
    except OSError:
        pass  # not mounted — nothing to clean up

    try:
        vfs = _create_vfs()
        storage.mount(vfs, "/sd")
        entries = os.listdir("/sd")
        print(f"SD card mounted at /sd — {len(entries)} file(s)")
        return True
    except OSError as exc:
        print(f"SD card mount failed: {exc}")
        return False


def verify_sd() -> tuple:
    """Full diagnostic verification of the SD card.

    Handles the case where boot.py's C-level VFS registration persists
    into this VM (EBUSY on mount).  Confirms the mount is a real SD card
    (≥ 10 MB) rather than the CIRCUITPY flash directory.  Writes a test
    file to confirm write access.

    Returns:
        (ok: bool, detail: str, entries: list | None)
            ok      — True if the SD card is usable
            detail  — human-readable status / error description
            entries — directory listing of /sd, or None on failure
    """
    try:
        vfs = _create_vfs()
        already_mounted = False
        try:
            storage.mount(vfs, "/sd")
        except OSError as exc:
            if exc.errno == 16:  # EBUSY — boot.py C-level mount persists
                already_mounted = True
            else:
                raise

        # Confirm /sd is the real SD card, not the CIRCUITPY sd/ sub-folder.
        try:
            st = os.statvfs("/sd")
            total_bytes = st[1] * st[2]
        except Exception:  # noqa: BLE001
            total_bytes = 0

        if total_bytes < _MIN_REAL_SD_BYTES:
            return (
                False,
                f"/sd is CIRCUITPY flash ({total_bytes // 1024} KB) — "
                "real SD card not mounted. Check boot.py and wiring.",
                None,
            )

        src = "boot.py C-level mount (EBUSY)" if already_mounted else "mounted at /sd"

        with open("/sd/mounted.txt", "w") as f:
            f.write("SD card mounted OK\n")
            f.flush()

        entries = os.listdir("/sd")
        return True, src, entries

    except Exception as exc:  # noqa: BLE001
        return False, str(exc), None
