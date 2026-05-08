"""OLED display controller for FeatherWeather.

Drives the Adafruit 128×64 OLED FeatherWing #4650 (SH1107) with a six-page
scrolling UI. Buttons A / B cycle pages; Button C forces an immediate redraw.

Call ``displayio.release_displays()`` **before** constructing this class
(typically at the very start of ``main()`` in code.py).

Button pin assignments (hardwired on the FeatherWing PCB):
    Button A  board.D9  → previous page
    Button B  board.D6  → next page
    Button C  board.D5  → force redraw
"""

from __future__ import annotations

import time

import board
import digitalio
import displayio
import i2cdisplaybus
import terminalio
from adafruit_display_text import label
from adafruit_displayio_sh1107 import (
    DISPLAY_OFFSET_ADAFRUIT_FEATHERWING_OLED_4650,
    SH1107,
)

__all__ = ["DisplayController", "DisplayState"]

_WIDTH: int = 128
_HEIGHT: int = 64
_OLED_ADDR: int = 0x3C
_WHITE: int = 0xFFFFFF
_DEBOUNCE_S: float = 0.08

# Label y-positions (pixels from top; terminalio.FONT glyphs are ~8 px tall)
_Y_TITLE: int = 4
_Y_L1: int = 18
_Y_L2: int = 30
_Y_L3: int = 42
_Y_L4: int = 54


class DisplayState:
    """Mutable snapshot of all sensor readings shown on the display.

    All fields default to ``None``; the display renders ``---`` for None values.
    Update these fields after each sensor cycle and call
    ``DisplayController.render()`` to refresh the screen.
    """

    def __init__(self) -> None:
        # GPS / status
        self.gps_has_fix: bool = False
        self.gps_satellites: int | None = None
        self.gps_utc_h: int | None = None
        self.gps_utc_m: int | None = None
        self.gps_utc_s: int | None = None
        self.gps_altitude_m: float | None = None
        # Temperature & Humidity (SHTC3)
        self.temp_c: float | None = None
        self.humidity_pct: float | None = None
        # Barometric (BMP390)
        self.baro_temp_c: float | None = None
        self.pressure_hpa: float | None = None
        self.sea_level_pressure_hpa: float | None = None
        # Air quality (HM3301)
        self.pm_1_0: int | None = None
        self.pm_2_5: int | None = None
        self.pm_10: int | None = None
        # Wind direction (SEN0482) + speed (SEN0483)
        self.wind_dir_deg: float | None = None
        self.wind_dir_label: str | None = None
        self.wind_speed_ms: float | None = None
        self.wind_beaufort: str | None = None
        # Rainfall (SEN0575)
        self.rain_cumulative_mm: float | None = None
        self.rain_window_mm: float | None = None
        # Illuminance (SEN0644)
        self.lux: float | None = None
        # Monotonic timestamp of the last completed sensor cycle
        self.last_read_s: float | None = None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _fmt(value, fmt: str, fallback: str = "---") -> str:
    """Format *value* with *fmt*, returning *fallback* when value is None."""
    if value is None:
        return fallback
    try:
        return fmt.format(value)
    except (ValueError, TypeError):
        return fallback


def _lbl(text: str, x: int, y: int) -> label.Label:
    return label.Label(terminalio.FONT, text=str(text), x=x, y=y, color=_WHITE)


def _make_button(pin) -> digitalio.DigitalInOut:
    btn = digitalio.DigitalInOut(pin)
    btn.direction = digitalio.Direction.INPUT
    btn.pull = digitalio.Pull.UP
    return btn


# ---------------------------------------------------------------------------
# DisplayController
# ---------------------------------------------------------------------------


class DisplayController:
    """SH1107 OLED + three-button navigation controller.

    Args:
        i2c: shared ``busio.I2C`` instance (same bus used by all I2C sensors).
             ``displayio.release_displays()`` must have been called before
             this constructor runs.
    """

    def __init__(self, i2c) -> None:
        display_bus = i2cdisplaybus.I2CDisplayBus(i2c, device_address=_OLED_ADDR)
        self._display = SH1107(
            display_bus,
            width=_WIDTH,
            height=_HEIGHT,
            display_offset=DISPLAY_OFFSET_ADAFRUIT_FEATHERWING_OLED_4650,
            rotation=0,
        )

        self._btn_a = _make_button(board.D9)   # previous page
        self._btn_b = _make_button(board.D6)   # next page
        self._btn_c = _make_button(board.D5)   # force redraw

        # Per-button debounce timestamps
        self._dbn: list[float] = [0.0, 0.0, 0.0]

        self.state = DisplayState()
        self._page: int = 0
        self._pages = [
            self._p_status,
            self._p_temp_humidity,
            self._p_pressure,
            self._p_air_quality,
            self._p_wind,
            self._p_rain_light,
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(self) -> None:
        """Redraw the current page."""
        self._pages[self._page]()

    def poll_buttons(self) -> None:
        """Sample buttons and navigate / redraw on debounced press."""
        now = time.monotonic()
        buttons = (self._btn_a, self._btn_b, self._btn_c)
        for i, btn in enumerate(buttons):
            if not btn.value and (now - self._dbn[i]) > _DEBOUNCE_S:
                self._dbn[i] = now
                if i == 0:
                    self._page = (self._page - 1) % len(self._pages)
                    self.render()
                elif i == 1:
                    self._page = (self._page + 1) % len(self._pages)
                    self.render()
                else:
                    self.render()

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    def _p_status(self) -> None:
        s = self.state
        fix = "FIX" if s.gps_has_fix else "no fix"
        sats = f" ({s.gps_satellites} sats)" if s.gps_satellites is not None else ""
        utc = (
            f"UTC: {s.gps_utc_h:02d}:{s.gps_utc_m:02d}:{s.gps_utc_s:02d}"
            if s.gps_has_fix and s.gps_utc_h is not None
            else "UTC: ---"
        )
        age = (
            f"Upd: {int(time.monotonic() - s.last_read_s)}s ago"
            if s.last_read_s is not None
            else "Upd: waiting..."
        )
        self._draw(
            "STATUS",
            f"GPS: {fix}{sats}",
            utc,
            f"Alt: {_fmt(s.gps_altitude_m, '{:.1f} m')}",
            age,
        )

    def _p_temp_humidity(self) -> None:
        s = self.state
        self._draw(
            "TEMP & HUMIDITY",
            f"Temp:  {_fmt(s.temp_c, '{:.1f} C')}",
            f"Hum:   {_fmt(s.humidity_pct, '{:.1f} %RH')}",
            f"BMP T: {_fmt(s.baro_temp_c, '{:.1f} C')}",
        )

    def _p_pressure(self) -> None:
        s = self.state
        self._draw(
            "PRESSURE",
            f"Press: {_fmt(s.pressure_hpa, '{:.2f} hPa')}",
            f"SLP:   {_fmt(s.sea_level_pressure_hpa, '{:.2f} hPa')}",
            f"Alt:   {_fmt(s.gps_altitude_m, '{:.1f} m')}",
        )

    def _p_air_quality(self) -> None:
        s = self.state
        self._draw(
            "AIR QUALITY",
            f"PM1.0: {_fmt(s.pm_1_0, '{} ug/m3')}",
            f"PM2.5: {_fmt(s.pm_2_5, '{} ug/m3')}",
            f"PM10:  {_fmt(s.pm_10, '{} ug/m3')}",
        )

    def _p_wind(self) -> None:
        s = self.state
        dir_str = (
            f"{_fmt(s.wind_dir_deg, '{:.0f}')} {s.wind_dir_label}"
            if s.wind_dir_label is not None
            else "---"
        )
        self._draw(
            "WIND",
            f"Dir:   {dir_str}",
            f"Speed: {_fmt(s.wind_speed_ms, '{:.1f} m/s')}",
            f"       {s.wind_beaufort or '---'}",
        )

    def _p_rain_light(self) -> None:
        s = self.state
        self._draw(
            "RAIN & LIGHT",
            f"Total: {_fmt(s.rain_cumulative_mm, '{:.2f} mm')}",
            f"1hr:   {_fmt(s.rain_window_mm, '{:.2f} mm')}",
            f"Light: {_fmt(s.lux, '{:.0f} lux')}",
        )

    # ------------------------------------------------------------------
    # Rendering helper
    # ------------------------------------------------------------------

    def _draw(self, title: str, *lines: str) -> None:
        """Build a displayio Group with title, up to 4 content lines, and a
        page indicator, then assign it as the display's root group."""
        n = len(self._pages)
        indicator = f"{self._page + 1}/{n}"

        group = displayio.Group()

        # Title on the left, page indicator on the right — same y row
        group.append(_lbl(title, 0, _Y_TITLE))
        ind_x = max(0, _WIDTH - len(indicator) * 6)
        group.append(_lbl(indicator, ind_x, _Y_TITLE))

        # Content lines
        y_positions = (_Y_L1, _Y_L2, _Y_L3, _Y_L4)
        for i, line in enumerate(lines[:4]):
            group.append(_lbl(line, 0, y_positions[i]))

        self._display.root_group = group
