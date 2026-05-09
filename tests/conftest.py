"""Shared pytest configuration for FeatherWeather tests.

All CircuitPython hardware modules are stubbed into sys.modules here, before
any featherweather imports can happen.  This lets host-Python tests import and
exercise the library code without physical hardware or Adafruit Blinka.

Stubs live at the module level so they apply to every test in the session.
Tests that need to control a stub's behaviour should call
``sys.modules['module_name'].SomeAttr = ...`` or use ``unittest.mock.patch``
for the duration of a single test.
"""

import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Hardware stub modules
# ---------------------------------------------------------------------------

_STUB_NAMES = [
    "board",
    "busio",
    "digitalio",
    "displayio",
    "i2cdisplaybus",
    "terminalio",
    "neopixel",
    "adafruit_bmp3xx",
    "adafruit_shtc3",
    "adafruit_gps",
    "adafruit_bus_device",
    "adafruit_bus_device.i2c_device",
    "adafruit_displayio_sh1107",
    "adafruit_display_text",
    "adafruit_display_text.label",
    "adafruit_pcf8523",
    "adafruit_pcf8523.pcf8523",
    "adafruit_ntp",
    "wifi",
    "socketpool",
    "storage",
    "sdcardio",
    "supervisor",
    "microcontroller",
    "analogio",
]

for _name in _STUB_NAMES:
    if _name not in sys.modules:
        sys.modules[_name] = MagicMock()

# Ensure sub-package attributes point at their sys.modules entry so that
# ``from adafruit_bus_device.i2c_device import I2CDevice`` works.
sys.modules["adafruit_bus_device"].i2c_device = sys.modules[
    "adafruit_bus_device.i2c_device"
]
sys.modules["adafruit_display_text"].label = sys.modules["adafruit_display_text.label"]

# ---------------------------------------------------------------------------
# board pin stubs
# ---------------------------------------------------------------------------
# MagicMock auto-creates attributes on first access, but we name a few pins
# explicitly so repr() is readable in assertion failures.

_board = sys.modules["board"]
for _pin in (
    "SCL",
    "SDA",
    "TX",
    "RX",
    "NEOPIXEL",
    "A0",
    "A1",
    "A2",
    "A6",
    "A7",
    "A8",
    "D12",
    "D13",
    "D27",
    "D33",
    "MOSI",
    "MISO",
    "SCK",
):
    if not hasattr(_board, _pin) or isinstance(getattr(_board, _pin), MagicMock):
        setattr(_board, _pin, MagicMock(name=f"board.{_pin}"))

# ---------------------------------------------------------------------------
# digitalio Pull stub
# ---------------------------------------------------------------------------
_digitalio = sys.modules["digitalio"]
_digitalio.Pull = MagicMock()
_digitalio.Pull.UP = MagicMock(name="Pull.UP")
_digitalio.Direction = MagicMock()
_digitalio.Direction.INPUT = MagicMock(name="Direction.INPUT")
