"""Built-in NeoPixel indicator for FeatherWeather.

Wraps the single NeoPixel present on the ESP32 Feather V2 (``board.NEOPIXEL``)
for visual pass/fail feedback during diagnostic runs.

Construction is always safe — if the ``neopixel`` module or the hardware pin
is unavailable all methods silently no-op, so callers need no guard logic::

    pixel = NeoPixelIndicator()
    pixel.flash_pass()           # green flash
    pixel.flash_fail()           # red flash
    pixel.flash_summary(8, 1)    # summary pattern based on pass/fail counts
    pixel.deinit()               # release hardware before reset

Color constants are exposed as class attributes::

    NeoPixelIndicator.GREEN
    NeoPixelIndicator.RED
    NeoPixelIndicator.YELLOW
    NeoPixelIndicator.OFF
"""

import time

__all__ = ["NeoPixelIndicator"]

# Default brightness — low enough to be non-blinding on a desk.
_DEFAULT_BRIGHTNESS: float = 0.2


class NeoPixelIndicator:
    """Single-pixel NeoPixel controller for diagnostic pass/fail signalling.

    All public methods are no-ops when the hardware is unavailable, so no
    ``if pixel.available`` guards are needed in calling code.

    Class-level color tuples (R, G, B) at the configured brightness level:
    """

    GREEN: tuple = (0, 50, 0)
    RED: tuple = (50, 0, 0)
    YELLOW: tuple = (50, 50, 0)
    OFF: tuple = (0, 0, 0)

    def __init__(self, brightness: float = _DEFAULT_BRIGHTNESS) -> None:
        """Initialise the NeoPixel.  Silently degrades if hardware is absent.

        Args:
            brightness: overall brightness scale (0.0 – 1.0).  Modifying this
                        does *not* rescale the color tuples above — they are
                        already tuned for 0.2 brightness.
        """
        self._pixel = None
        try:
            import board  # noqa: PLC0415
            import neopixel as _neopixel  # noqa: PLC0415

            self._pixel = _neopixel.NeoPixel(
                board.NEOPIXEL, 1, brightness=brightness, auto_write=True
            )
            self._pixel[0] = self.OFF
        except Exception:  # noqa: BLE001
            pass  # no NeoPixel in this build / wiring — degrade gracefully

    @property
    def available(self) -> bool:
        """True when the NeoPixel was successfully initialised."""
        return self._pixel is not None

    # ------------------------------------------------------------------
    # Low-level flash primitive
    # ------------------------------------------------------------------

    def flash(
        self,
        color: tuple,
        count: int = 1,
        on_ms: int = 200,
        off_ms: int = 100,
    ) -> None:
        """Flash *color* for *count* pulses.

        Args:
            color:  (R, G, B) tuple.  Use the class constants for consistency.
            count:  number of flashes.
            on_ms:  milliseconds the pixel stays lit per flash.
            off_ms: milliseconds the pixel stays off between flashes.
        """
        if self._pixel is None:
            return
        for _ in range(count):
            self._pixel[0] = color
            time.sleep(on_ms / 1000)
            self._pixel[0] = self.OFF
            time.sleep(off_ms / 1000)

    # ------------------------------------------------------------------
    # Semantic helpers
    # ------------------------------------------------------------------

    def flash_pass(self, count: int = 1) -> None:
        """Single green flash — call after each passing test."""
        self.flash(self.GREEN, count=count)

    def flash_fail(self, count: int = 1) -> None:
        """Single red flash — call after each failing test."""
        self.flash(self.RED, count=count)

    def flash_summary(self, pass_count: int, fail_count: int) -> None:
        """End-of-run summary flash pattern.

        - All tests passed → green × total
        - < 50 % failed    → yellow × fail_count
        - ≥ 50 % failed    → red × fail_count

        Args:
            pass_count: number of tests that passed.
            fail_count: number of tests that failed.
        """
        total = pass_count + fail_count
        if total == 0:
            return
        fail_pct = fail_count / total * 100
        if fail_count == 0:
            self.flash(self.GREEN, count=total, on_ms=300, off_ms=150)
        elif fail_pct < 50.0:
            self.flash(self.YELLOW, count=fail_count, on_ms=300, off_ms=150)
        else:
            self.flash(self.RED, count=fail_count, on_ms=300, off_ms=150)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def deinit(self) -> None:
        """Turn the pixel off and release the hardware pin."""
        if self._pixel is None:
            return
        try:
            self._pixel.fill(self.OFF)
            self._pixel.deinit()
        except Exception:  # noqa: BLE001
            pass
        self._pixel = None
