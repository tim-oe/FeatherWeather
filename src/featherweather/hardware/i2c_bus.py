"""Shared I2C bus singleton for FeatherWeather.

All sensors and the OLED display share one busio.I2C instance on
board.SCL / board.SDA.  The bus frequency defaults to 20 kHz to satisfy
the HM3301 air-quality sensor (its maximum); every other sensor on the
bus tolerates this reduced speed.

Environment variable:
    I2C_FREQ_HZ  bus clock frequency in Hz  (default 20000)
"""

import os

import board
import busio

__all__ = ["get_i2c"]

_i2c = None


def get_i2c():
    """Return the shared I2C bus, creating it on the first call.

    Creates ``busio.I2C`` at the configured frequency (default 20 kHz for
    HM3301 compatibility).  If SCL is already claimed by the board's native
    I2C peripheral (raised as ``ValueError: SCL in use``), falls back to
    ``board.I2C()`` so the rest of the system still functions — note that
    the frequency may differ from ``I2C_FREQ_HZ`` in that case.
    """
    global _i2c  # noqa: PLW0603
    if _i2c is None:
        freq = int(os.getenv("I2C_FREQ_HZ") or 20_000)
        try:
            _i2c = busio.I2C(board.SCL, board.SDA, frequency=freq)
        except ValueError:
            _i2c = board.I2C()
    return _i2c
