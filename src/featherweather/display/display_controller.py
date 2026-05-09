"""OLED display controller for FeatherWeather.

Drives the Adafruit 128×64 OLED FeatherWing #4650 (SH1107) with an eight-page
scrolling UI controlled by three physical buttons on the wing.

``DisplayController`` holds a ``WeatherPayload`` as its single source of truth.
All display pages read directly from the payload — adding a new sensor only
requires adding a page here; no separate state container needs updating.

Button layout and function (buttons are stacked vertically on the wing):

    Physical position  FeatherWing label  board pin  GPIO   Action
    ─────────────────  ─────────────────  ─────────  ─────  ──────────────
    TOP                C                  board.A6   37     next page →
    MIDDLE             B                  board.A7   32     toggle display on/off
    BOTTOM             A                  board.A8   15     ← previous page
                                          (board.A6 is input-only; FeatherWing pulls up)

Environment variables:
    OLED_ADDR   I2C address of the SH1107 display, hex or decimal (default 0x3C)
"""

from __future__ import annotations

import os
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

from featherweather.hardware.i2c_bus import get_i2c
from featherweather.storage.weather_payload import WeatherPayload

__all__ = ["DisplayController"]

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


def _safe(obj, attr, default=None):
    """Return ``getattr(obj, attr)`` or *default* when *obj* is None."""
    return getattr(obj, attr, default) if obj is not None else default


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

    Acquires the shared I2C bus via ``featherweather.hardware.i2c_bus.get_i2c()``
    and calls ``displayio.release_displays()`` before claiming the display bus.

    Class-level I2C identity — read by ``diagnostic.py`` to populate the bus-scan
    address map without duplicating the address in multiple places::

        DisplayController.I2C_ADDR  # 0x3C
        DisplayController.LABEL     # "SH1107 (OLED FeatherWing #4650)"

    Data model:
        ``self.payload`` is a ``WeatherPayload`` — the single source of truth
        for all sensor readings shown on screen.  Update it with::

            display.update(new_payload)      # full sensor cycle result
            display.update_gps(gps_data)     # GPS-only refresh (5 Hz polling)

    Button actions:
        TOP    (C, board.A6) — advance to next page; turns display on if off
        MIDDLE (B, board.A7) — toggle display on / off
        BOTTOM (A, board.A8) — go to previous page; turns display on if off
    """

    I2C_ADDR: int = _OLED_ADDR
    LABEL: str = "SH1107 (OLED FeatherWing #4650)"

    def __init__(self) -> None:
        _env = os.getenv("OLED_ADDR")
        addr = int(_env, 0) if _env else _OLED_ADDR
        displayio.release_displays()
        display_bus = i2cdisplaybus.I2CDisplayBus(get_i2c(), device_address=addr)
        self._display = SH1107(
            display_bus,
            width=_WIDTH,
            height=_HEIGHT,
            display_offset=DISPLAY_OFFSET_ADAFRUIT_FEATHERWING_OLED_4650,
            rotation=270,
        )

        # GPIOs run top-to-bottom as A6→A7→A8 (C→B→A) on the ESP32 Feather V2
        self._btn_c = _make_button(board.A6)  # TOP    button C — next page (GPIO37)
        self._btn_b = _make_button(board.A7)  # MIDDLE button B — toggle display
        self._btn_a = _make_button(board.A8)  # BOTTOM button A — previous page

        # Per-button state (indexed: 0=C/top, 1=B/mid, 2=A/bottom)
        self._prev: list[bool] = [True, True, True]
        self._dbn: list[float] = [0.0, 0.0, 0.0]
        self._display_on: bool = True
        self._last_activity_s: float = time.monotonic()

        # Single source of truth for all sensor data shown on screen.
        # Starts as an empty payload (all sensors None) so pages always have
        # a valid object to read from without None guards on self.payload itself.
        self.payload: WeatherPayload = WeatherPayload()

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
            self._p_system,
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(self) -> None:
        """Redraw the current sensor page. No-ops when the display is toggled off."""
        if not self._display_on:
            return
        self._pages[self._page]()

    def update(self, payload: WeatherPayload) -> None:
        """Replace the current payload with *payload* and refresh the display.

        Called once per sensor cycle.  Because ``WeatherPayload`` is the
        single data container, no field-by-field mapping is needed — simply
        pass the freshly-built payload here.

        Args:
            payload: the completed observation from the latest sensor cycle.
        """
        self.payload = payload
        self.render()

    def update_gps(self, gps_data) -> None:
        """Update only the GPS slice of the current payload.

        Called at ~5 Hz from the main loop so GPS state stays current between
        sensor cycles without triggering a full display redraw.

        Args:
            gps_data: ``GpsData`` instance from ``GpsReader.read()``.
        """
        self.payload.gps = gps_data

    def display(
        self,
        line1: str = "",
        line2: str = "",
        line3: str = "",
        line4: str = "",
    ) -> None:
        """Show up to four lines of text directly on the OLED.

        Bypasses the sensor-page system; useful for status messages,
        diagnostic splash screens, and summary readouts.

        Line positions:
            line1 → title row  (y = 4)
            line2 → row 1      (y = 18)
            line3 → row 2      (y = 30)
            line4 → row 3      (y = 42)
        """
        group = displayio.Group()
        for text, y in (
            (line1, _Y_TITLE),
            (line2, _Y_L1),
            (line3, _Y_L2),
            (line4, _Y_L3),
        ):
            if text:
                group.append(_lbl(text, 0, y))
        self._display.root_group = group
        self._display_on = True
        self._last_activity_s = time.monotonic()

    def blank(self) -> None:
        """Blank the display (all pixels off) without changing the current page."""
        self._display_on = False
        self._display.root_group = displayio.Group()

    def any_button_pressed(self) -> bool:
        """Return True if any OLED button is currently held down (active low)."""
        return not self._btn_a.value or not self._btn_b.value or not self._btn_c.value

    def poll_buttons(self) -> None:
        """Sample buttons and act on a single debounced press.

        Triggers on the falling edge (idle-high → pressed-low) so each physical
        click fires exactly once regardless of hold duration.

        Auto-off: blanks after ``_AUTO_OFF_S`` seconds of inactivity.
        """
        now = time.monotonic()

        if self._display_on and (now - self._last_activity_s) >= _AUTO_OFF_S:
            self._display_on = False
            self._display.root_group = displayio.Group()

        buttons = (self._btn_c, self._btn_b, self._btn_a)  # top → mid → bottom
        for i, btn in enumerate(buttons):
            val = btn.value
            pressed = self._prev[i] and not val
            self._prev[i] = val
            if not pressed or (now - self._dbn[i]) < _DEBOUNCE_S:
                continue
            self._dbn[i] = now
            self._last_activity_s = now
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
    # Pages — all read directly from self.payload
    # ------------------------------------------------------------------

    def _p_status(self) -> None:
        p = self.payload
        gps = p.gps
        ip = p.ip_address or "---"
        date = (
            f"{gps.timestamp_utc.tm_year:04d}-"
            f"{gps.timestamp_utc.tm_mon:02d}-"
            f"{gps.timestamp_utc.tm_mday:02d}"
            if gps is not None and gps.has_fix and gps.timestamp_utc is not None
            else "---"
        )
        utc = (
            f"{gps.timestamp_utc.tm_hour:02d}:"
            f"{gps.timestamp_utc.tm_min:02d}:"
            f"{gps.timestamp_utc.tm_sec:02d} UTC"
            if gps is not None and gps.has_fix and gps.timestamp_utc is not None
            else "---"
        )
        age = (
            f"{int(time.monotonic() - p.last_read_s)}s ago"
            if p.last_read_s is not None
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
        gps = self.payload.gps
        fix = "FIX" if _safe(gps, "has_fix", False) else "no fix"
        sats = _fmt(_safe(gps, "satellites"), "{}")
        lat = _fmt(_safe(gps, "latitude"), "{:.5f}")
        lon = _fmt(_safe(gps, "longitude"), "{:.5f}")
        alt = _fmt(_safe(gps, "altitude_m"), "{:.1f} m")
        self._draw(
            "GPS",
            f"Status: {fix}  {sats} sats",
            f"Lat:    {lat}",
            f"Lon:    {lon}",
            f"Alt:    {alt}",
        )

    def _p_temp_humidity(self) -> None:
        th = self.payload.temp_humidity
        baro = self.payload.barometric
        self._draw(
            "TEMP & HUMIDITY",
            f"Temp:  {_fmt(_safe(th,   'temperature'),     '{:.1f} C')}",
            f"Hum:   {_fmt(_safe(th,   'relative_humidity'), '{:.1f} %RH')}",
            f"BMP T: {_fmt(_safe(baro, 'temperature'),     '{:.1f} C')}",
        )

    def _p_pressure(self) -> None:
        baro = self.payload.barometric
        gps = self.payload.gps
        self._draw(
            "PRESSURE",
            f"Press: {_fmt(_safe(baro, 'pressure'),           '{:.2f} hPa')}",
            f"SLP:   {_fmt(_safe(baro, 'sea_level_pressure'), '{:.2f} hPa')}",
            f"Alt:   {_fmt(_safe(gps,  'altitude_m'),         '{:.1f} m')}",
        )

    def _p_air_quality(self) -> None:
        aq = self.payload.air_quality
        self._draw(
            "AIR QUALITY",
            f"PM1.0: {_fmt(_safe(aq, 'pm_1_0_atm'), '{} ug/m3')}",
            f"PM2.5: {_fmt(_safe(aq, 'pm_2_5_atm'), '{} ug/m3')}",
            f"PM10:  {_fmt(_safe(aq, 'pm_10_atm'),  '{} ug/m3')}",
        )

    def _p_wind(self) -> None:
        wd = self.payload.wind_direction
        ws = self.payload.wind_speed
        deg = _safe(wd, "degrees")
        lbl = _safe(wd, "direction_label")
        dir_str = f"{_fmt(deg, '{:.0f}')} {lbl}" if lbl is not None else "---"
        self._draw(
            "WIND",
            f"Dir:   {dir_str}",
            f"Speed: {_fmt(_safe(ws, 'speed_ms'),  '{:.1f} m/s')}",
            f"       {_safe(ws, 'beaufort') or '---'}",
        )

    def _p_rain_light(self) -> None:
        rain = self.payload.rainfall
        illum = self.payload.illuminance
        self._draw(
            "RAIN & LIGHT",
            f"Total: {_fmt(_safe(rain,  'cumulative_rainfall_mm'), '{:.2f} mm')}",
            f"1hr:   {_fmt(_safe(rain,  'rainfall_window_mm'),     '{:.2f} mm')}",
            f"Light: {_fmt(_safe(illum, 'lux'),                    '{:.0f} lux')}",
        )

    def _p_sound(self) -> None:
        mic = self.payload.microphone
        db = _safe(mic, "db_spl")
        if db is None:
            qual = "---"
        elif db < 30:
            qual = "Very quiet"
        elif db < 50:
            qual = "Quiet"
        elif db < 70:
            qual = "Moderate"
        elif db < 85:
            qual = "Loud"
        else:
            qual = "Very loud"
        self._draw(
            "SOUND LEVEL",
            f"dB SPL: {_fmt(db, '{:.1f} dB')}",
            f"Level:  {qual}",
        )

    def _p_system(self) -> None:
        sys = self.payload.system
        used_b = _safe(sys, "heap_used_b")
        free_b = _safe(sys, "heap_free_b")
        pct = _fmt(_safe(sys, "heap_pct"), "{}%", "---")
        used_str = f"{used_b // 1024} KB" if used_b is not None else "---"
        free_str = f"{free_b // 1024} KB" if free_b is not None else "---"

        uptime = _safe(sys, "uptime_s")
        if uptime is None:
            up_str = "---"
        elif uptime < 3600:
            up_str = f"{int(uptime)}s"
        else:
            up_str = f"{int(uptime / 3600)}h {int((uptime % 3600) / 60)}m"

        cpu_mhz = _safe(sys, "cpu_freq_mhz")
        cpu_str = f"{cpu_mhz} MHz" if cpu_mhz is not None else "---"

        try:
            fw = os.uname().release
        except Exception:  # noqa: BLE001
            fw = "---"

        self._draw(
            "SYSTEM",
            f"Used:  {used_str} ({pct})",
            f"Free:  {free_str}",
            f"CPU:   {cpu_str}  FW:{fw}",
            f"Up:    {up_str}",
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
        group.append(_lbl(title, 0, _Y_TITLE))
        ind_x = max(0, _WIDTH - len(indicator) * 6)
        group.append(_lbl(indicator, ind_x, _Y_TITLE))

        y_positions = (_Y_L1, _Y_L2, _Y_L3, _Y_L4)
        for i, line in enumerate(lines[:4]):
            group.append(_lbl(line, 0, y_positions[i]))

        self._display.root_group = group
