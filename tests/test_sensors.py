"""Tests for sensor base classes and concrete I2C sensor readers.

Hardware (busio.I2C, Adafruit drivers) is mocked at the sys.modules level by
conftest.py, so these tests run on host Python without any physical device.

Pattern for each reader:
    1. Patch get_i2c() and the driver constructor so __init__ succeeds.
    2. Exercise read(payload) and assert the payload field is set correctly.
    3. Test class-level constants (_I2C_ADDR, _LABEL) directly.
"""

from unittest.mock import MagicMock, patch

import pytest

from featherweather.gps.gps_data import GpsData
from featherweather.sensors.air_quality.air_quality_reader import AirQualityReader
from featherweather.sensors.barometric.barometric_reader import BarometricReader
from featherweather.sensors.i2c_sensor_base import I2cSensorBase
from featherweather.sensors.sensor_base import SensorBase
from featherweather.sensors.temp_humidity.temp_humidity_reader import TempHumidityReader
from featherweather.storage.weather_payload import WeatherPayload

# ---------------------------------------------------------------------------
# SensorBase
# ---------------------------------------------------------------------------


class TestSensorBase:
    def test_read_raises_not_implemented(self):
        sensor = SensorBase()
        with pytest.raises(NotImplementedError, match="must implement read"):
            sensor.read(WeatherPayload())

    def test_subclass_must_override_read(self):
        class _Good(SensorBase):
            def read(self, payload):
                payload.ip_address = "ok"

        s = _Good()
        p = WeatherPayload()
        s.read(p)
        assert p.ip_address == "ok"


# ---------------------------------------------------------------------------
# I2cSensorBase
# ---------------------------------------------------------------------------


class TestI2cSensorBase:
    def test_init_device_raises_not_implemented(self):
        """Instantiating the base class directly should raise NotImplementedError
        from _init_device() (via get_i2c + super().__init__)."""
        with patch(
            "featherweather.sensors.i2c_sensor_base.get_i2c", return_value=MagicMock()
        ):
            with pytest.raises(
                NotImplementedError, match="must implement _init_device"
            ):
                I2cSensorBase()

    def test_read_raises_not_implemented(self):
        """read() from SensorBase must also be overridden."""

        class _Stub(I2cSensorBase):
            def _init_device(self, i2c, address):
                pass  # no-op

        with patch(
            "featherweather.sensors.i2c_sensor_base.get_i2c", return_value=MagicMock()
        ):
            stub = _Stub()

        with pytest.raises(NotImplementedError, match="must implement read"):
            stub.read(WeatherPayload())

    def test_address_override_from_constructor(self):
        captured = {}

        class _Stub(I2cSensorBase):
            _I2C_ADDR = 0x77

            def _init_device(self, i2c, address):
                captured["addr"] = address

        with patch(
            "featherweather.sensors.i2c_sensor_base.get_i2c", return_value=MagicMock()
        ):
            _Stub(address=0x76)

        assert captured["addr"] == 0x76

    def test_default_address_used_when_not_overridden(self):
        captured = {}

        class _Stub(I2cSensorBase):
            _I2C_ADDR = 0x42

            def _init_device(self, i2c, address):
                captured["addr"] = address

        with patch(
            "featherweather.sensors.i2c_sensor_base.get_i2c", return_value=MagicMock()
        ):
            _Stub()

        assert captured["addr"] == 0x42


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_i2c_patch():
    """Return a patch that makes get_i2c() return a fresh MagicMock."""
    return patch(
        "featherweather.sensors.i2c_sensor_base.get_i2c",
        return_value=MagicMock(),
    )


# ---------------------------------------------------------------------------
# BarometricReader
# ---------------------------------------------------------------------------


class TestBarometricReader:
    def test_class_constants(self):
        assert BarometricReader._I2C_ADDR == 0x77
        assert "BMP390" in BarometricReader._LABEL
        assert "Barometric" in BarometricReader._LABEL

    def test_sea_level_pressure_from_altitude_known_value(self):
        """At sea level (altitude = 0) SLP should equal raw pressure."""
        slp = BarometricReader.sea_level_pressure_from_altitude(1013.25, 0.0)
        assert slp == pytest.approx(1013.25, rel=1e-4)

    def test_sea_level_pressure_increases_with_altitude(self):
        """SLP must be higher than raw pressure when sensor is above sea level."""
        raw = 985.0
        altitude = 220.0
        slp = BarometricReader.sea_level_pressure_from_altitude(raw, altitude)
        assert slp > raw

    def test_read_sets_payload_barometric(self):
        mock_sensor = MagicMock()
        mock_sensor.pressure = 1000.0
        mock_sensor.temperature = 18.5

        _BMP = "featherweather.sensors.barometric.barometric_reader.adafruit_bmp3xx.BMP3XX_I2C"  # noqa: E501
        with _make_i2c_patch():
            with patch(_BMP, return_value=mock_sensor):
                reader = BarometricReader()

        payload = WeatherPayload()
        reader.read(payload)

        assert payload.barometric is not None
        assert payload.barometric.pressure == pytest.approx(1000.0)
        assert payload.barometric.temperature == pytest.approx(18.5)

    def test_read_computes_slp_when_gps_altitude_present(self):
        mock_sensor = MagicMock()
        mock_sensor.pressure = 985.0
        mock_sensor.temperature = 15.0

        _BMP = "featherweather.sensors.barometric.barometric_reader.adafruit_bmp3xx.BMP3XX_I2C"  # noqa: E501
        with _make_i2c_patch():
            with patch(_BMP, return_value=mock_sensor):
                reader = BarometricReader()

        gps = GpsData()
        gps.altitude_m = 200.0
        payload = WeatherPayload(gps=gps)
        reader.read(payload)

        assert payload.barometric.sea_level_pressure is not None
        assert payload.barometric.sea_level_pressure > 985.0

    def test_read_skips_slp_when_no_gps(self):
        mock_sensor = MagicMock()
        mock_sensor.pressure = 1000.0
        mock_sensor.temperature = 20.0

        _BMP = "featherweather.sensors.barometric.barometric_reader.adafruit_bmp3xx.BMP3XX_I2C"  # noqa: E501
        with _make_i2c_patch():
            with patch(_BMP, return_value=mock_sensor):
                reader = BarometricReader()

        payload = WeatherPayload()  # no gps
        reader.read(payload)
        assert payload.barometric.sea_level_pressure is None

    def test_env_var_overrides_address(self, monkeypatch):
        monkeypatch.setenv("BARO_ADDR", "0x76")
        captured = {}

        _BMP = "featherweather.sensors.barometric.barometric_reader.adafruit_bmp3xx.BMP3XX_I2C"  # noqa: E501
        with _make_i2c_patch():
            with patch(_BMP) as mock_cls:
                mock_cls.return_value = MagicMock()
                mock_cls.side_effect = (
                    lambda i2c, address: captured.update({"addr": address})
                    or MagicMock()
                )
                BarometricReader()

        assert captured.get("addr") == 0x76


# ---------------------------------------------------------------------------
# TempHumidityReader
# ---------------------------------------------------------------------------


class TestTempHumidityReader:
    def test_class_constants(self):
        assert TempHumidityReader._I2C_ADDR == 0x70
        assert "SHTC3" in TempHumidityReader._LABEL

    def test_read_sets_payload_temp_humidity(self):
        mock_sensor = MagicMock()
        mock_sensor.measurements = (23.1, 65.4)

        _SHTC3 = "featherweather.sensors.temp_humidity.temp_humidity_reader.adafruit_shtc3.SHTC3"  # noqa: E501
        with _make_i2c_patch():
            with patch(_SHTC3, return_value=mock_sensor):
                reader = TempHumidityReader()

        payload = WeatherPayload()
        reader.read(payload)

        assert payload.temp_humidity is not None
        assert payload.temp_humidity.temperature == pytest.approx(23.1)
        assert payload.temp_humidity.relative_humidity == pytest.approx(65.4)

    def test_read_leaves_other_payload_fields_untouched(self):
        mock_sensor = MagicMock()
        mock_sensor.measurements = (20.0, 50.0)

        _SHTC3 = "featherweather.sensors.temp_humidity.temp_humidity_reader.adafruit_shtc3.SHTC3"  # noqa: E501
        with _make_i2c_patch():
            with patch(_SHTC3, return_value=mock_sensor):
                reader = TempHumidityReader()

        payload = WeatherPayload(ip_address="10.0.0.1")
        reader.read(payload)

        assert payload.ip_address == "10.0.0.1"
        assert payload.barometric is None


# ---------------------------------------------------------------------------
# AirQualityReader — CRC parsing and internal logic
# ---------------------------------------------------------------------------


def _make_aq_frame(
    pm_1_0_std=0,
    pm_2_5_std=0,
    pm_10_std=0,
    pm_1_0_atm=0,
    pm_2_5_atm=0,
    pm_10_atm=0,
) -> bytearray:
    """Build a valid 29-byte HM3301 frame with correct CRC."""
    buf = bytearray(29)
    buf[4], buf[5] = pm_1_0_std >> 8, pm_1_0_std & 0xFF
    buf[6], buf[7] = pm_2_5_std >> 8, pm_2_5_std & 0xFF
    buf[8], buf[9] = pm_10_std >> 8, pm_10_std & 0xFF
    buf[10], buf[11] = pm_1_0_atm >> 8, pm_1_0_atm & 0xFF
    buf[12], buf[13] = pm_2_5_atm >> 8, pm_2_5_atm & 0xFF
    buf[14], buf[15] = pm_10_atm >> 8, pm_10_atm & 0xFF
    buf[28] = sum(buf[:28]) & 0xFF
    return buf


class TestAirQualityReader:
    def test_class_constants(self):
        assert AirQualityReader._I2C_ADDR == 0x40
        assert "HM3301" in AirQualityReader._LABEL

    def test_parse_extracts_pm_values(self):
        frame = _make_aq_frame(
            pm_1_0_std=10,
            pm_2_5_std=20,
            pm_10_std=30,
            pm_1_0_atm=5,
            pm_2_5_atm=15,
            pm_10_atm=25,
        )
        data = AirQualityReader._parse(frame)
        assert data.pm_1_0_std == 10
        assert data.pm_2_5_std == 20
        assert data.pm_10_std == 30
        assert data.pm_1_0_atm == 5
        assert data.pm_2_5_atm == 15
        assert data.pm_10_atm == 25

    def test_parse_two_byte_big_endian(self):
        """Values that require both high and low bytes are decoded correctly."""
        frame = _make_aq_frame(pm_2_5_atm=0x01FF)  # 511
        data = AirQualityReader._parse(frame)
        assert data.pm_2_5_atm == 511

    def test_crc_valid_accepts_good_frame(self):
        frame = _make_aq_frame(pm_2_5_atm=22)
        assert AirQualityReader._crc_valid(frame)

    def test_crc_valid_rejects_bad_checksum(self):
        frame = _make_aq_frame(pm_2_5_atm=22)
        frame[28] ^= 0xFF
        assert not AirQualityReader._crc_valid(frame)

    def test_read_sets_payload_air_quality(self):
        frame = _make_aq_frame(pm_1_0_atm=8, pm_2_5_atm=22, pm_10_atm=45)

        mock_dev = MagicMock()
        mock_dev.__enter__ = MagicMock(return_value=mock_dev)
        mock_dev.__exit__ = MagicMock(return_value=False)
        mock_dev.readinto.side_effect = lambda buf: buf.__setitem__(slice(None), frame)

        with _make_i2c_patch():
            with patch(
                "featherweather.sensors.air_quality.air_quality_reader.I2CDevice",
                return_value=mock_dev,
            ):
                reader = AirQualityReader(retry=1, wait_sec=0)

        payload = WeatherPayload()
        reader.read(payload)

        assert payload.air_quality is not None
        assert payload.air_quality.pm_1_0_atm == 8
        assert payload.air_quality.pm_2_5_atm == 22
        assert payload.air_quality.pm_10_atm == 45
        mock_dev.write.assert_called_once()

    def test_warm_up_select_sent_once_across_crc_retries(self):
        good_frame = _make_aq_frame(pm_2_5_atm=22)
        bad_frame = _make_aq_frame(pm_2_5_atm=22)
        bad_frame[28] ^= 0xFF
        frames = [bad_frame, good_frame]

        mock_dev = MagicMock()
        mock_dev.__enter__ = MagicMock(return_value=mock_dev)
        mock_dev.__exit__ = MagicMock(return_value=False)
        mock_dev.readinto.side_effect = lambda buf: buf.__setitem__(
            slice(None), frames.pop(0)
        )

        with _make_i2c_patch():
            with patch(
                "featherweather.sensors.air_quality.air_quality_reader.I2CDevice",
                return_value=mock_dev,
            ):
                with patch(
                    "featherweather.sensors.air_quality.air_quality_reader.time.sleep"
                ):
                    reader = AirQualityReader(retry=2, wait_sec=0)

        payload = WeatherPayload()
        reader.read(payload)

        assert payload.air_quality.pm_2_5_atm == 22
        mock_dev.write.assert_called_once()
        assert mock_dev.readinto.call_count == 2

    def test_read_raises_after_all_crc_failures(self):
        bad_frame = bytearray(
            29
        )  # all zeros → CRC = 0, but buf[28] is also 0 → actually valid!
        # Make it fail: set buf[28] to wrong CRC
        bad_frame[28] = 0xFF

        mock_dev = MagicMock()
        mock_dev.__enter__ = MagicMock(return_value=mock_dev)
        mock_dev.__exit__ = MagicMock(return_value=False)
        mock_dev.readinto.side_effect = lambda buf: buf.__setitem__(
            slice(None), bad_frame
        )

        with _make_i2c_patch():
            with patch(
                "featherweather.sensors.air_quality.air_quality_reader.I2CDevice",
                return_value=mock_dev,
            ):
                with patch(
                    "featherweather.sensors.air_quality.air_quality_reader.time.sleep"
                ):
                    reader = AirQualityReader(retry=2, wait_sec=0)

        with pytest.raises(ValueError, match="CRC failed"):
            reader.read(WeatherPayload())

        mock_dev.write.assert_called_once()
        assert mock_dev.readinto.call_count == 2


# ---------------------------------------------------------------------------
# GpsReader
# ---------------------------------------------------------------------------


class TestGpsReader:
    """Tests for GpsReader.read() which translates adafruit_gps state → GpsData."""

    def _make_reader_with_mock_gps(self, has_fix=False, **attrs):
        """Create a GpsReader with a mocked internal _gps object."""
        from featherweather.gps.gps_reader import GpsReader

        mock_uart = MagicMock()
        mock_gps = MagicMock()
        mock_gps.has_fix = has_fix
        mock_gps.fix_quality = 1 if has_fix else 0
        for k, v in attrs.items():
            setattr(mock_gps, k, v)

        with patch("featherweather.gps.gps_reader.busio.UART", return_value=mock_uart):
            with patch(
                "featherweather.gps.gps_reader.adafruit_gps.GPS", return_value=mock_gps
            ):
                reader = GpsReader()

        return reader, mock_gps

    def test_read_no_fix_returns_empty_gps_data(self):
        reader, _ = self._make_reader_with_mock_gps(has_fix=False)
        data = reader.read()
        assert data.has_fix is False
        assert data.latitude is None
        assert data.longitude is None
        assert data.altitude_m is None

    def test_read_with_fix_populates_all_fields(self):
        import time as _time

        ts = _time.struct_time((2025, 8, 1, 10, 0, 0, 3, 213, 0))
        reader, _ = self._make_reader_with_mock_gps(
            has_fix=True,
            latitude=41.878,
            longitude=-87.629,
            altitude_m=182.0,
            satellites=9,
            speed_knots=0.2,
            track_angle_deg=270.0,
            timestamp_utc=ts,
            horizontal_dilution=1.1,
        )
        data = reader.read()
        assert data.has_fix is True
        assert data.fix_quality == 1
        assert data.latitude == pytest.approx(41.878)
        assert data.longitude == pytest.approx(-87.629)
        assert data.altitude_m == pytest.approx(182.0)
        assert data.satellites == 9
        assert data.speed_knots == pytest.approx(0.2)
        assert data.hdop == pytest.approx(1.1)
        assert data.timestamp_utc is ts

    def test_update_delegates_to_gps(self):
        reader, mock_gps = self._make_reader_with_mock_gps()
        mock_gps.update.return_value = True
        result = reader.update()
        mock_gps.update.assert_called_once()
        assert result is True

    def test_read_fix_quality_defaults_to_zero_when_none(self):
        """fix_quality is None on some firmware; reader should default to 0."""
        reader, mock_gps = self._make_reader_with_mock_gps(has_fix=False)
        mock_gps.fix_quality = None
        data = reader.read()
        assert data.fix_quality == 0
