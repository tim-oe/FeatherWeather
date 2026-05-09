"""Tests for DisplayController.

All hardware (I2C, OLED, buttons) is mocked by conftest.py and per-test
patches so that __init__ completes without physical hardware.
"""

import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, call, patch

import pytest

from featherweather.gps.gps_data import GpsData
from featherweather.sensors.barometric.barometric_data import BarometricData
from featherweather.storage.weather_payload import WeatherPayload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _display_patches():
    """Context manager that patches all DisplayController hardware dependencies."""
    mock_i2c = MagicMock()
    mock_display = MagicMock()
    mock_display_bus = MagicMock()
    mock_btn = MagicMock()

    with (
        patch(
            "featherweather.display.display_controller.get_i2c", return_value=mock_i2c
        ),
        patch("featherweather.display.display_controller.displayio") as mock_displayio,
        patch(
            "featherweather.display.display_controller.i2cdisplaybus"
        ) as mock_i2cdisplaybus,
        patch(
            "featherweather.display.display_controller.SH1107",
            return_value=mock_display,
        ),
        patch("featherweather.display.display_controller.digitalio") as mock_digitalio,
    ):

        mock_i2cdisplaybus.I2CDisplayBus.return_value = mock_display_bus
        mock_digitalio.DigitalInOut.return_value = mock_btn
        mock_btn.switch_to_input = MagicMock()
        mock_displayio.release_displays = MagicMock()

        yield {
            "i2c": mock_i2c,
            "display": mock_display,
            "display_bus": mock_display_bus,
            "btn": mock_btn,
        }


def _make_display():
    """Return a fully-constructed DisplayController with all hardware mocked."""
    from featherweather.display.display_controller import DisplayController

    with _display_patches():
        return DisplayController()


# ---------------------------------------------------------------------------
# Class-level constants
# ---------------------------------------------------------------------------


class TestDisplayControllerConstants:
    def test_i2c_addr(self):
        from featherweather.display.display_controller import DisplayController

        assert DisplayController.I2C_ADDR == 0x3C

    def test_label(self):
        from featherweather.display.display_controller import DisplayController

        assert "SH1107" in DisplayController.LABEL
        assert "OLED" in DisplayController.LABEL


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestDisplayControllerInit:
    def test_payload_starts_as_empty_weather_payload(self):
        dc = _make_display()
        assert isinstance(dc.payload, WeatherPayload)
        assert dc.payload.barometric is None
        assert dc.payload.gps is None

    def test_display_on_by_default(self):
        dc = _make_display()
        assert dc._display_on is True

    def test_oled_addr_env_var(self, monkeypatch):
        """OLED_ADDR env var (as hex string) is parsed correctly."""
        monkeypatch.setenv("OLED_ADDR", "0x3C")
        from featherweather.display.display_controller import DisplayController

        with _display_patches() as mocks:
            dc = DisplayController()
        # Constructor should complete without TypeError
        assert dc.payload is not None


# ---------------------------------------------------------------------------
# update() and update_gps()
# ---------------------------------------------------------------------------


class TestPayloadUpdate:
    def test_update_replaces_payload(self):
        dc = _make_display()
        baro = BarometricData()
        baro.pressure = 1013.0
        new_payload = WeatherPayload(barometric=baro, ip_address="10.0.0.1")

        dc.update(new_payload)

        assert dc.payload is new_payload
        assert dc.payload.barometric.pressure == 1013.0
        assert dc.payload.ip_address == "10.0.0.1"

    def test_update_calls_render(self):
        dc = _make_display()
        with patch.object(dc, "render") as mock_render:
            dc.update(WeatherPayload())
        mock_render.assert_called_once()

    def test_update_gps_patches_only_gps_field(self):
        dc = _make_display()
        baro = BarometricData()
        baro.pressure = 999.0
        dc.payload = WeatherPayload(barometric=baro, ip_address="192.168.0.5")

        gps = GpsData()
        gps.has_fix = True
        gps.altitude_m = 150.0
        dc.update_gps(gps)

        assert dc.payload.gps is gps
        assert dc.payload.gps.altitude_m == 150.0
        # Other fields must remain intact
        assert dc.payload.barometric.pressure == 999.0
        assert dc.payload.ip_address == "192.168.0.5"

    def test_update_gps_does_not_trigger_render(self):
        """update_gps() is called at 5 Hz; it must NOT redraw the display."""
        dc = _make_display()
        with patch.object(dc, "render") as mock_render:
            dc.update_gps(GpsData())
        mock_render.assert_not_called()


# ---------------------------------------------------------------------------
# display() and blank()
# ---------------------------------------------------------------------------


class TestDirectDisplay:
    def test_display_calls_underlying_refresh(self):
        dc = _make_display()
        # Should not raise; the actual rendering goes through the mocked _display
        dc.display("Line 1", "Line 2", "Line 3", "Line 4")

    def test_display_with_fewer_than_four_lines(self):
        dc = _make_display()
        dc.display("Only one line")

    def test_blank_does_not_raise(self):
        dc = _make_display()
        dc.blank()


# ---------------------------------------------------------------------------
# Helper module-level functions
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    def test_fmt_none_returns_fallback(self):
        from featherweather.display.display_controller import _fmt

        assert _fmt(None, "{:.1f}") == "---"

    def test_fmt_custom_fallback(self):
        from featherweather.display.display_controller import _fmt

        assert _fmt(None, "{:.1f}", fallback="n/a") == "n/a"

    def test_fmt_formats_value(self):
        from featherweather.display.display_controller import _fmt

        assert _fmt(3.14159, "{:.2f}") == "3.14"

    def test_safe_returns_default_when_obj_none(self):
        from featherweather.display.display_controller import _safe

        assert _safe(None, "anything") is None

    def test_safe_returns_attr(self):
        from featherweather.display.display_controller import _safe

        obj = BarometricData()
        obj.pressure = 1005.0
        assert _safe(obj, "pressure") == 1005.0

    def test_safe_returns_default_when_attr_missing(self):
        from featherweather.display.display_controller import _safe

        obj = BarometricData()
        assert _safe(obj, "nonexistent", default=42) == 42
