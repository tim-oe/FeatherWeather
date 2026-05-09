"""Tests for WeatherPayload and the reflection-based JSON serializer."""

import json
import time

import pytest

from featherweather.gps.gps_data import GpsData
from featherweather.sensors.barometric.barometric_data import BarometricData
from featherweather.sensors.temp_humidity.temp_humidity_data import TempHumidityData
from featherweather.storage.serializer import to_json
from featherweather.storage.weather_payload import WeatherPayload

# ---------------------------------------------------------------------------
# WeatherPayload
# ---------------------------------------------------------------------------


class TestWeatherPayload:
    def test_all_defaults_none(self):
        p = WeatherPayload()
        for field in (
            "barometric",
            "temp_humidity",
            "air_quality",
            "rainfall",
            "wind_direction",
            "wind_speed",
            "illuminance",
            "microphone",
            "gps",
            "last_read_s",
            "ip_address",
        ):
            assert getattr(p, field) is None, f"{field} should default to None"

    def test_construct_with_values(self):
        baro = BarometricData()
        baro.pressure = 1013.0

        th = TempHumidityData()
        th.temperature = 22.0

        gps = GpsData()
        gps.has_fix = True
        gps.altitude_m = 200.0

        p = WeatherPayload(
            barometric=baro,
            temp_humidity=th,
            gps=gps,
            last_read_s=12345.6,
            ip_address="192.168.1.50",
        )

        assert p.barometric.pressure == 1013.0
        assert p.temp_humidity.temperature == 22.0
        assert p.gps.altitude_m == 200.0
        assert p.last_read_s == 12345.6
        assert p.ip_address == "192.168.1.50"

    def test_carry_forward_gps_on_reassignment(self):
        """Simulates the sensor-cycle pattern: carry gps from previous payload."""
        gps = GpsData()
        gps.has_fix = True
        gps.altitude_m = 300.0

        p1 = WeatherPayload(gps=gps)
        p2 = WeatherPayload(gps=p1.gps, ip_address="10.0.0.1")

        assert p2.gps.altitude_m == 300.0
        assert p2.ip_address == "10.0.0.1"
        assert p2.barometric is None

    def test_gps_field_can_be_replaced(self):
        p = WeatherPayload()
        assert p.gps is None
        g = GpsData()
        g.has_fix = True
        p.gps = g
        assert p.gps.has_fix is True

    def test_to_json_returns_valid_json(self):
        baro = BarometricData()
        baro.pressure = 1000.0
        baro.temperature = 15.0
        p = WeatherPayload(barometric=baro, ip_address="192.168.0.1")
        result = json.loads(p.to_json())
        assert result["barometric"]["pressure"] == 1000.0
        assert result["barometric"]["temperature"] == 15.0
        assert result["ip_address"] == "192.168.0.1"
        assert result["gps"] is None


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------


class TestSerializer:
    def test_none_serializes_to_null(self):
        result = json.loads(to_json(None))
        assert result is None

    def test_primitives_pass_through(self):
        assert json.loads(to_json(42)) == 42
        assert json.loads(to_json(3.14)) == pytest.approx(3.14)
        assert json.loads(to_json("hello")) == "hello"
        assert json.loads(to_json(True)) is True

    def test_simple_object(self):
        baro = BarometricData()
        baro.pressure = 1015.5
        baro.temperature = 19.0
        result = json.loads(to_json(baro))
        assert result["pressure"] == pytest.approx(1015.5)
        assert result["temperature"] == pytest.approx(19.0)
        assert result["sea_level_pressure"] is None

    def test_nested_object(self):
        p = WeatherPayload()
        th = TempHumidityData()
        th.temperature = 25.0
        th.relative_humidity = 70.0
        p.temp_humidity = th
        result = json.loads(to_json(p))
        assert result["temp_humidity"]["temperature"] == pytest.approx(25.0)
        assert result["temp_humidity"]["relative_humidity"] == pytest.approx(70.0)

    def test_struct_time_to_iso8601(self):
        st = time.struct_time((2025, 12, 31, 23, 59, 58, 2, 365, 0))
        result = json.loads(to_json(st))
        assert result == "2025-12-31T23:59:58Z"

    def test_gps_data_with_timestamp(self):
        g = GpsData()
        g.has_fix = True
        g.latitude = 40.0
        g.longitude = -75.0
        g.altitude_m = 50.0
        g.timestamp_utc = time.struct_time((2025, 6, 1, 12, 0, 0, 6, 152, 0))
        result = json.loads(to_json(g))
        assert result["latitude"] == pytest.approx(40.0)
        assert result["timestamp_utc"] == "2025-06-01T12:00:00Z"

    def test_full_empty_payload_round_trips(self):
        p = WeatherPayload()
        result = json.loads(p.to_json())
        # All sensor fields should be null in JSON
        for field in (
            "barometric",
            "temp_humidity",
            "air_quality",
            "rainfall",
            "wind_direction",
            "wind_speed",
            "illuminance",
            "microphone",
            "gps",
        ):
            assert result[field] is None
