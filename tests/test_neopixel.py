"""Tests for NeoPixelIndicator.

The neopixel and board modules are already mocked at the sys.modules level by
conftest.py.  Each test controls the mock's behaviour as needed.
"""

import sys
from unittest.mock import MagicMock, call, patch

from featherweather.display.neopixel_indicator import NeoPixelIndicator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pixel_mock():
    """Return a list-like MagicMock that mimics a NeoPixel object."""
    mock = MagicMock()
    mock.__setitem__ = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestNeoPixelIndicatorInit:
    def test_initialises_when_hardware_available(self):
        mock_pixel = _make_pixel_mock()
        with patch.dict(
            sys.modules,
            {
                "neopixel": MagicMock(**{"NeoPixel.return_value": mock_pixel}),
            },
        ):
            indicator = NeoPixelIndicator.__new__(NeoPixelIndicator)
            indicator._pixel = mock_pixel
            assert indicator.available is True

    def test_available_false_when_import_fails(self):
        """If neopixel raises on import, available should be False."""
        # Temporarily replace the neopixel stub with one whose NeoPixel
        # constructor raises, simulating missing hardware.
        bad_neopixel = MagicMock()
        bad_neopixel.NeoPixel.side_effect = RuntimeError("no hardware")

        with patch.dict(sys.modules, {"neopixel": bad_neopixel}):
            indicator = NeoPixelIndicator()

        assert indicator.available is False

    def test_available_true_after_successful_init(self):
        mock_pixel = _make_pixel_mock()
        with patch.dict(
            sys.modules,
            {
                "neopixel": MagicMock(**{"NeoPixel.return_value": mock_pixel}),
            },
        ):
            indicator = NeoPixelIndicator()

        assert indicator.available is True


# ---------------------------------------------------------------------------
# Color constants
# ---------------------------------------------------------------------------


class TestColorConstants:
    def test_green_is_greenish(self):
        g = NeoPixelIndicator.GREEN
        assert g[1] > 0  # green channel
        assert g[0] == 0  # no red
        assert g[2] == 0  # no blue

    def test_red_is_reddish(self):
        r = NeoPixelIndicator.RED
        assert r[0] > 0
        assert r[1] == 0
        assert r[2] == 0

    def test_yellow_has_red_and_green(self):
        y = NeoPixelIndicator.YELLOW
        assert y[0] > 0
        assert y[1] > 0
        assert y[2] == 0

    def test_off_is_zero(self):
        assert NeoPixelIndicator.OFF == (0, 0, 0)


# ---------------------------------------------------------------------------
# flash / flash_pass / flash_fail
# ---------------------------------------------------------------------------


class TestFlash:
    def _indicator_with_pixel(self):
        ind = NeoPixelIndicator.__new__(NeoPixelIndicator)
        ind._pixel = _make_pixel_mock()
        return ind

    def test_flash_no_op_when_pixel_none(self):
        ind = NeoPixelIndicator.__new__(NeoPixelIndicator)
        ind._pixel = None
        # Should not raise
        ind.flash(NeoPixelIndicator.GREEN)

    def test_flash_sets_color_then_off(self):
        ind = self._indicator_with_pixel()
        with patch("featherweather.display.neopixel_indicator.time") as mock_time:
            mock_time.sleep = MagicMock()
            ind.flash(NeoPixelIndicator.GREEN, count=1, on_ms=200, off_ms=100)

        calls = ind._pixel.__setitem__.call_args_list
        assert calls[0] == call(0, NeoPixelIndicator.GREEN)
        assert calls[1] == call(0, NeoPixelIndicator.OFF)

    def test_flash_repeats_for_count(self):
        ind = self._indicator_with_pixel()
        with patch("featherweather.display.neopixel_indicator.time") as mock_time:
            mock_time.sleep = MagicMock()
            ind.flash(NeoPixelIndicator.RED, count=3)

        # 3 flashes → 6 set-item calls (on + off each time)
        assert ind._pixel.__setitem__.call_count == 6

    def test_flash_pass_uses_green(self):
        ind = self._indicator_with_pixel()
        with patch.object(ind, "flash") as mock_flash:
            ind.flash_pass()
        mock_flash.assert_called_once_with(NeoPixelIndicator.GREEN, count=1)

    def test_flash_fail_uses_red(self):
        ind = self._indicator_with_pixel()
        with patch.object(ind, "flash") as mock_flash:
            ind.flash_fail()
        mock_flash.assert_called_once_with(NeoPixelIndicator.RED, count=1)

    def test_flash_pass_with_count(self):
        ind = self._indicator_with_pixel()
        with patch.object(ind, "flash") as mock_flash:
            ind.flash_pass(count=3)
        mock_flash.assert_called_once_with(NeoPixelIndicator.GREEN, count=3)


# ---------------------------------------------------------------------------
# flash_summary
# ---------------------------------------------------------------------------


class TestFlashSummary:
    def _indicator(self):
        ind = NeoPixelIndicator.__new__(NeoPixelIndicator)
        ind._pixel = _make_pixel_mock()
        return ind

    def test_all_pass_flashes_green(self):
        ind = self._indicator()
        with patch.object(ind, "flash") as mock_flash:
            ind.flash_summary(pass_count=5, fail_count=0)
        args = mock_flash.call_args
        assert args[0][0] == NeoPixelIndicator.GREEN
        assert args[1]["count"] == 5

    def test_all_fail_flashes_red(self):
        ind = self._indicator()
        with patch.object(ind, "flash") as mock_flash:
            ind.flash_summary(pass_count=0, fail_count=4)
        args = mock_flash.call_args
        assert args[0][0] == NeoPixelIndicator.RED
        assert args[1]["count"] == 4

    def test_minority_fail_flashes_yellow(self):
        ind = self._indicator()
        with patch.object(ind, "flash") as mock_flash:
            ind.flash_summary(pass_count=8, fail_count=1)  # 11% fail
        args = mock_flash.call_args
        assert args[0][0] == NeoPixelIndicator.YELLOW
        assert args[1]["count"] == 1

    def test_majority_fail_flashes_red(self):
        ind = self._indicator()
        with patch.object(ind, "flash") as mock_flash:
            ind.flash_summary(pass_count=2, fail_count=5)  # 71% fail
        args = mock_flash.call_args
        assert args[0][0] == NeoPixelIndicator.RED

    def test_exactly_50_pct_fail_flashes_red(self):
        ind = self._indicator()
        with patch.object(ind, "flash") as mock_flash:
            ind.flash_summary(pass_count=3, fail_count=3)  # 50% fail
        args = mock_flash.call_args
        assert args[0][0] == NeoPixelIndicator.RED

    def test_zero_tests_is_no_op(self):
        ind = self._indicator()
        with patch.object(ind, "flash") as mock_flash:
            ind.flash_summary(pass_count=0, fail_count=0)
        mock_flash.assert_not_called()


# ---------------------------------------------------------------------------
# deinit
# ---------------------------------------------------------------------------


class TestDeinit:
    def test_deinit_fills_off_and_deinits(self):
        pixel = _make_pixel_mock()
        ind = NeoPixelIndicator.__new__(NeoPixelIndicator)
        ind._pixel = pixel
        ind.deinit()
        pixel.fill.assert_called_once_with(NeoPixelIndicator.OFF)
        pixel.deinit.assert_called_once()
        assert ind._pixel is None

    def test_deinit_no_op_when_pixel_none(self):
        ind = NeoPixelIndicator.__new__(NeoPixelIndicator)
        ind._pixel = None
        ind.deinit()  # should not raise

    def test_deinit_survives_hardware_error(self):
        pixel = _make_pixel_mock()
        pixel.fill.side_effect = RuntimeError("hardware gone")
        ind = NeoPixelIndicator.__new__(NeoPixelIndicator)
        ind._pixel = pixel
        ind.deinit()  # must not propagate
