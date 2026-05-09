"""Tests for SystemData and SystemReader.

SystemReader uses only stdlib modules (gc, time) plus an optional
``microcontroller`` import, so no hardware mocking is needed beyond
what conftest.py already stubs in sys.modules.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from featherweather.storage.weather_payload import WeatherPayload
from featherweather.system.system_data import SystemData
from featherweather.system.system_reader import SystemReader

# ---------------------------------------------------------------------------
# SystemData
# ---------------------------------------------------------------------------


class TestSystemData:
    def test_defaults(self):
        d = SystemData()
        assert d.heap_used_b == 0
        assert d.heap_free_b == 0
        assert d.uptime_s == 0.0
        assert d.cpu_freq_mhz is None

    def test_heap_total_b(self):
        d = SystemData()
        d.heap_used_b = 100_000
        d.heap_free_b = 1_900_000
        assert d.heap_total_b == 2_000_000

    def test_heap_pct_normal(self):
        d = SystemData()
        d.heap_used_b = 200_000
        d.heap_free_b = 800_000
        assert d.heap_pct == 20

    def test_heap_pct_zero_total(self):
        d = SystemData()
        d.heap_used_b = 0
        d.heap_free_b = 0
        assert d.heap_pct == 0

    def test_heap_pct_fully_used(self):
        d = SystemData()
        d.heap_used_b = 1_000
        d.heap_free_b = 0
        assert d.heap_pct == 100

    def test_fields_assignable(self):
        d = SystemData()
        d.heap_used_b = 98_304
        d.heap_free_b = 1_953_792
        d.uptime_s = 21_861.0
        d.cpu_freq_mhz = 240
        assert d.heap_used_b == 98_304
        assert d.heap_free_b == 1_953_792
        assert d.uptime_s == 21_861.0
        assert d.cpu_freq_mhz == 240

    def test_repr_contains_key_values(self):
        d = SystemData()
        d.heap_used_b = 100 * 1024
        d.heap_free_b = 1900 * 1024
        d.uptime_s = 300.0
        r = repr(d)
        assert "100 KB" in r
        assert "1900 KB" in r
        assert "300s" in r
        assert "SystemData(" in r


# ---------------------------------------------------------------------------
# SystemReader
# ---------------------------------------------------------------------------

_GC_PATCH = "featherweather.system.system_reader.gc"


def _gc_mock(used: int = 0, free: int = 0) -> MagicMock:
    """Return a gc mock with mem_alloc/mem_free returning fixed values."""
    m = MagicMock()
    m.mem_alloc.return_value = used
    m.mem_free.return_value = free
    return m


class TestSystemReader:
    def test_is_sensor_base_subclass(self):
        from featherweather.sensors.sensor_base import SensorBase

        assert issubclass(SystemReader, SensorBase)

    def test_read_populates_payload_system(self):
        reader = SystemReader()
        payload = WeatherPayload()
        with patch(_GC_PATCH, _gc_mock()):
            reader.read(payload)
        assert payload.system is not None
        assert isinstance(payload.system, SystemData)

    def test_read_captures_heap_values(self):
        reader = SystemReader()
        payload = WeatherPayload()
        with patch(_GC_PATCH, _gc_mock(used=100_000, free=1_900_000)):
            reader.read(payload)
        assert payload.system.heap_used_b == 100_000
        assert payload.system.heap_free_b == 1_900_000

    def test_read_captures_uptime(self):
        reader = SystemReader()
        payload = WeatherPayload()
        with (
            patch(_GC_PATCH, _gc_mock()),
            patch("featherweather.system.system_reader.time") as mock_time,
        ):
            mock_time.monotonic.return_value = 12345.6
            reader.read(payload)
        assert payload.system.uptime_s == pytest.approx(12345.6)

    def test_read_cpu_freq_when_microcontroller_available(self):
        mock_mc = MagicMock()
        mock_mc.cpu.frequency = 240_000_000
        reader = SystemReader()
        payload = WeatherPayload()
        with (
            patch(_GC_PATCH, _gc_mock()),
            patch.dict(sys.modules, {"microcontroller": mock_mc}),
        ):
            reader.read(payload)
        assert payload.system.cpu_freq_mhz == 240

    def test_read_cpu_freq_none_when_microcontroller_absent(self):
        reader = SystemReader()
        payload = WeatherPayload()
        with (
            patch(_GC_PATCH, _gc_mock()),
            patch.dict(sys.modules, {"microcontroller": None}),
        ):
            reader.read(payload)
        assert payload.system.cpu_freq_mhz is None

    def test_read_cpu_freq_none_on_attribute_error(self):
        # spec=[] means no attributes are allowed → accessing .cpu raises AttributeError
        mock_mc = MagicMock(spec=[])
        reader = SystemReader()
        payload = WeatherPayload()
        with (
            patch(_GC_PATCH, _gc_mock()),
            patch.dict(sys.modules, {"microcontroller": mock_mc}),
        ):
            reader.read(payload)
        assert payload.system.cpu_freq_mhz is None

    def test_read_calls_gc_collect(self):
        reader = SystemReader()
        payload = WeatherPayload()
        mock_gc = _gc_mock()
        with patch(_GC_PATCH, mock_gc):
            reader.read(payload)
        mock_gc.collect.assert_called_once()
