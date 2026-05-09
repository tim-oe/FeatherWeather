"""OLED display controller for FeatherWeather.

Drives the Adafruit 128×64 OLED FeatherWing #4650 (SH1107) with an eight-page
scrolling UI controlled by three physical buttons on the wing.

Button layout and function (buttons are stacked vertically on the wing):

    Physical position  FeatherWing label  board pin  GPIO   Action
    ─────────────────  ─────────────────  ─────────  ─────  ──────────────
    TOP                C                  board.A6   37     next page →
    MIDDLE             B                  board.A7   32     toggle display on/off
    BOTTOM             A                  board.A8   15     ← previous page
                                          (board.A6 is input-only; FeatherWing pulls up)

Note: on the ESP32 Feather V2 the FeatherWing button GPIOs run top-to-bottom
as A6→A7→A8 (C→B→A), which is the reverse of the alphabetical label order.
The Feather header pin physically labeled "5" is board.SCK (GPIO5) and is
permanently owned by the SPI bus (SD card) — not a button pin.
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
_AUTO_OFF_S: float = 15.0  # blank display after this many seconds of inactivity

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
        # Network
        self.ip_address: str | None = None
        # GPS / status
        self.gps_has_fix: bool = False
        self.gps_satellites: int | None = None
        self.gps_utc_h: int | None = None
        self.gps_utc_m: int | None = None
        self.gps_utc_s: int | None = None
        self.gps_utc_day: int | None = None
        self.gps_utc_month: int | None = None
        self.gps_utc_year: int | None = None
        self.gps_latitude: float | None = None
        self.gps_longitude: float | None = None
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
        # Sound level (SPH0645 I2S mic #3421)
        self.db_spl: float | None = None
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
    try:
        btn.switch_to_input(pull=digitalio.Pull.UP)
    except (ValueError, AttributeError):
        # Input-only GPIO (e.g. ESP32 GPIO37) has no internal pull resistor.
        # The OLED FeatherWing PCB provides external pull-ups on all button lines.
        btn.switch_to_input(pull=None)
    return btn


# ---------------------------------------------------------------------------
# DisplayController
# ---------------------------------------------------------------------------


class DisplayController:
    """SH1107 OLED + three-button navigation controller.

    The constructor calls ``displayio.release_displays()`` before claiming the
    display bus.  The caller must call ``displayio.release_displays()`` before
    creating the ``busio.I2C`` instance (to free SCL/SDA from any display left
    active by the previous code.py run), then pass that bus here.

    Button actions:
        TOP    (C, board.A6) — advance to next page; turns display on if off
        MIDDLE (B, board.A7) — toggle display on / off
        BOTTOM (A, board.A8) — go to previous page; turns display on if off
    """

    def __init__(self, i2c) -> None:
        displayio.release_displays()
        display_bus = i2cdisplaybus.I2CDisplayBus(i2c, device_address=_OLED_ADDR)
        self._display = SH1107(
            display_bus,
            width=_WIDTH,
            height=_HEIGHT,
            display_offset=DISPLAY_OFFSET_ADAFRUIT_FEATHERWING_OLED_4650,
            rotation=270,
        )

        # GPIOs run top-to-bottom as A6→A7→A8 (C→B→A) on the ESP32 Feather V2
        self._btn_c = _make_button(
            board.A6
        )  # TOP    button C — next page      (GPIO37, input-only)
        self._btn_b = _make_button(
            board.A7
        )  # MIDDLE button B — toggle display (GPIO32)
        self._btn_a = _make_button(
            board.A8
        )  # BOTTOM button A — previous page  (GPIO15)

        # Per-button state tracking (indexed: 0=C/top, 1=B/mid, 2=A/bottom)
        # _prev holds the last sampled value; True = released (pull-up idle high)
        self._prev: list[bool] = [True, True, True]
        self._dbn: list[float] = [0.0, 0.0, 0.0]
        self._display_on: bool = True
        self._last_activity_s: float = time.monotonic()

        self.state = DisplayState()
        self._page: int = 0
        self._pages = [
            self._p_status,
            self._p_gps_status,
            self._p_temp_humidity,
            self._p_pressure,
            self._p_air_quality,
            self._p_wind,
            self._p_rain_light,
            self._p_sound,
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(self) -> None:
        """Redraw the current page. No-ops when the display is toggled off."""
        if not self._display_on:
            return
        self._pages[self._page]()

    def poll_buttons(self) -> None:
        """Sample buttons and act on a single debounced press.

        Triggers on the falling edge only (idle-high → pressed-low transition)
        so each physical click fires exactly once regardless of hold duration.

        TOP    (C, board.A6) — next page
        MIDDLE (B, board.A7) — toggle display on / off
        BOTTOM (A, board.A8) — previous page

        Auto-off: the display blanks after _AUTO_OFF_S seconds of inactivity.
        Any button press wakes it.
        """
        now = time.monotonic()

        # Auto-off: blank the display after inactivity timeout
        if self._display_on and (now - self._last_activity_s) >= _AUTO_OFF_S:
            self._display_on = False
            self._display.root_group = displayio.Group()

        buttons = (self._btn_c, self._btn_b, self._btn_a)  # top → mid → bottom
        for i, btn in enumerate(buttons):
            val = btn.value
            pressed = self._prev[i] and not val  # falling edge: was high, now low
            self._prev[i] = val
            if not pressed or (now - self._dbn[i]) < _DEBOUNCE_S:
                continue
            self._dbn[i] = now
            self._last_activity_s = now  # any press resets the inactivity timer
            if i == 0:  # TOP (C) — next page
                self._display_on = True
                self._page = (self._page + 1) % len(self._pages)
                self.render()
            elif i == 1:  # MIDDLE (B) — toggle on/off
                self._display_on = not self._display_on
                if self._display_on:
                    self.render()
                else:
                    self._display.root_group = displayio.Group()
            else:  # BOTTOM (A) — previous page
                self._display_on = True
                self._page = (self._page - 1) % len(self._pages)
                self.render()

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    def _p_status(self) -> None:
        s = self.state
        ip = s.ip_address if s.ip_address is not None else "---"
        date = (
            f"{s.gps_utc_year:04d}-{s.gps_utc_month:02d}-{s.gps_utc_day:02d}"
            if s.gps_has_fix and s.gps_utc_year is not None
            else "---"
        )
        utc = (
            f"{s.gps_utc_h:02d}:{s.gps_utc_m:02d}:{s.gps_utc_s:02d} UTC"
            if s.gps_has_fix and s.gps_utc_h is not None
            else "---"
        )
        age = (
            f"{int(time.monotonic() - s.last_read_s)}s ago"
            if s.last_read_s is not None
            else "waiting..."
        )
        self._draw(
            "STATUS",
            f"IP:   {ip}",
            f"Date: {date}",
            f"Time: {utc}",
            f"Upd:  {age}",
        )

    def _p_gps_status(self) -> None:
        s = self.state
        fix = "FIX" if s.gps_has_fix else "no fix"
        sats = _fmt(s.gps_satellites, "{}")
        lat = _fmt(s.gps_latitude, "{:.5f}")
        lon = _fmt(s.gps_longitude, "{:.5f}")
        alt = _fmt(s.gps_altitude_m, "{:.1f} m")
        self._draw(
            "GPS",
            f"Status: {fix}  {sats} sats",
            f"Lat:    {lat}",
            f"Lon:    {lon}",
            f"Alt:    {alt}",
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

    def _p_sound(self) -> None:
        s = self.state
        # Qualitative label based on standard dB SPL ranges
        if s.db_spl is None:
            label = "---"
        elif s.db_spl < 30:
            label = "Very quiet"
        elif s.db_spl < 50:
            label = "Quiet"
        elif s.db_spl < 70:
            label = "Moderate"
        elif s.db_spl < 85:
            label = "Loud"
        else:
            label = "Very loud"
        self._draw(
            "SOUND LEVEL",
            f"dB SPL: {_fmt(s.db_spl, '{:.1f} dB')}",
            f"Level:  {label}",
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
