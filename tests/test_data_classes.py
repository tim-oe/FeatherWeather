"""Tests for all sensor data model classes.

These are pure-Python data containers with no hardware dependencies,
so no mocking is required beyond what conftest.py provides.
"""

import time

from featherweather.gps.gps_data import GpsData
from featherweather.sensors.air_quality.air_quality_data import AirQualityData
from featherweather.sensors.barometric.barometric_data import BarometricData
from featherweather.sensors.temp_humidity.temp_humidity_data import TempHumidityData

# ---------------------------------------------------------------------------
# BarometricData
# ---------------------------------------------------------------------------


class TestBarometricData:
    def test_defaults(self):
        d = BarometricData()
        assert d.pressure == 0.0
        assert d.temperature == 0.0
        assert d.sea_level_pressure is None

    def test_fields_assignable(self):
        d = BarometricData()
        d.pressure = 1013.25
        d.temperature = 22.5
        d.sea_level_pressure = 1015.0
        assert d.pressure == 1013.25
        assert d.temperature == 22.5
        assert d.sea_level_pressure == 1015.0

    def test_repr_without_slp(self):
        d = BarometricData()
        d.pressure = 1010.0
        d.temperature = 20.0
        r = repr(d)
        assert "1010.00 hPa" in r
        assert "20.00 C" in r
        assert "n/a" in r

    def test_repr_with_slp(self):
        d = BarometricData()
        d.pressure = 1010.0
        d.temperature = 20.0
        d.sea_level_pressure = 1015.5
        r = repr(d)
        assert "1015.50 hPa" in r
        assert "n/a" not in r


# ---------------------------------------------------------------------------
# TempHumidityData
# ---------------------------------------------------------------------------


class TestTempHumidityData:
    def test_defaults(self):
        d = TempHumidityData()
        assert d.temperature == 0.0
        assert d.relative_humidity == 0.0

    def test_fields_assignable(self):
        d = TempHumidityData()
        d.temperature = 21.3
        d.relative_humidity = 55.7
        assert d.temperature == 21.3
        assert d.relative_humidity == 55.7

    def test_repr(self):
        d = TempHumidityData()
        d.temperature = 18.0
        d.relative_humidity = 60.0
        r = repr(d)
        assert "18.00 C" in r
        assert "60.0 %RH" in r


# ---------------------------------------------------------------------------
# AirQualityData
# ---------------------------------------------------------------------------


class TestAirQualityData:
    def test_defaults(self):
        d = AirQualityData()
        for field in (
            "pm_1_0_std",
            "pm_2_5_std",
            "pm_10_std",
            "pm_1_0_atm",
            "pm_2_5_atm",
            "pm_10_atm",
        ):
            assert getattr(d, field) == 0

    def test_is_high_below_ceiling(self):
        d = AirQualityData()
        d.pm_1_0_std = 10
        d.pm_2_5_std = 20
        d.pm_10_std = 30
        d.pm_1_0_atm = 10
        d.pm_2_5_atm = 20
        d.pm_10_atm = 30
        assert not d.is_high(500)

    def test_is_high_one_channel_over(self):
        d = AirQualityData()
        d.pm_2_5_atm = 600  # exceeds default ceiling of 500
        assert d.is_high(500)

    def test_is_high_custom_ceiling(self):
        d = AirQualityData()
        d.pm_10_std = 150
        assert d.is_high(100)
        assert not d.is_high(200)

    def test_clamp_to_lower_replaces_high_values(self):
        high = AirQualityData()
        high.pm_2_5_std = 600
        high.pm_2_5_atm = 700
        high.pm_10_std = 50  # below ceiling

        other = AirQualityData()
        other.pm_2_5_std = 200
        other.pm_2_5_atm = 50
        other.pm_10_std = 30

        high.clamp_to_lower(other, ceiling=500)

        assert high.pm_2_5_std == 200  # clamped to lower
        assert high.pm_2_5_atm == 50  # clamped to lower
        assert high.pm_10_std == 50  # untouched — was below ceiling

    def test_clamp_to_lower_takes_min_when_both_over(self):
        """When both self and other exceed ceiling, keep the smaller."""
        a = AirQualityData()
        a.pm_1_0_std = 800

        b = AirQualityData()
        b.pm_1_0_std = 600

        a.clamp_to_lower(b, ceiling=500)
        assert a.pm_1_0_std == 600

    def test_repr(self):
        d = AirQualityData()
        d.pm_1_0_atm = 5
        d.pm_2_5_atm = 12
        d.pm_10_atm = 30
        r = repr(d)
        assert "pm1.0=5" in r
        assert "pm2.5=12" in r
        assert "pm10=30" in r


# ---------------------------------------------------------------------------
# GpsData
# ---------------------------------------------------------------------------


class TestGpsData:
    def test_defaults(self):
        d = GpsData()
        assert d.has_fix is False
        assert d.fix_quality == 0
        assert d.latitude is None
        assert d.longitude is None
        assert d.altitude_m is None
        assert d.satellites is None
        assert d.hdop is None

    def test_repr_no_fix(self):
        d = GpsData()
        assert repr(d) == "GpsData(no fix)"

    def test_repr_with_fix(self):
        d = GpsData()
        d.has_fix = True
        d.latitude = 41.878113
        d.longitude = -87.629799
        d.altitude_m = 182.0
        d.satellites = 8
        d.hdop = 1.2
        d.timestamp_utc = time.struct_time((2025, 6, 15, 14, 30, 0, 6, 166, 0))
        r = repr(d)
        assert "41.878113" in r
        assert "-87.629799" in r
        assert "182.0m" in r
        assert "sats=8" in r
        assert "14:30:00Z" in r

    def test_repr_with_fix_no_timestamp(self):
        d = GpsData()
        d.has_fix = True
        d.latitude = 0.0
        d.longitude = 0.0
        d.altitude_m = 0.0
        d.satellites = 4
        d.hdop = 2.0
        r = repr(d)
        assert "n/a" in r

    def test_fields_assignable(self):
        d = GpsData()
        d.has_fix = True
        d.latitude = 51.5074
        d.longitude = -0.1278
        d.altitude_m = 11.0
        d.speed_knots = 0.5
        d.track_angle_deg = 90.0
        assert d.latitude == 51.5074
        assert d.speed_knots == 0.5
