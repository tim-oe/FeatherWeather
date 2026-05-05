"""Air quality reader for CircuitPython (HM3301).

No Adafruit library exists for this sensor — uses raw I2C via adafruit_bus_device.
Implements CRC validation and retry logic from WeatherWatch.

IMPORTANT: HM3301 requires I2C bus speed <= 20 kHz:
    i2c = busio.I2C(board.SCL, board.SDA, frequency=20_000)

Ported from https://github.com/tim-oe/WeatherWatch
(weatherwatch/sensor/aqi/Hm3301Reader.py)
Reference: https://wiki.seeedstudio.com/Grove-Laser_PM2.5_Sensor-HM3301/

Usage:
    import busio, board
    i2c = busio.I2C(board.SCL, board.SDA, frequency=20_000)
    reader = AirQualityReader(i2c)
    data = reader.read()
"""

import time

from adafruit_bus_device.i2c_device import I2CDevice

from featherweather.sensors.air_quality.air_quality_data import AirQualityData

__all__ = ["AirQualityReader"]

_I2C_ADDR: int = 0x40
_SELECT_CMD: bytes = bytes([0x88])
_DATA_LEN: int = 29
_DEFAULT_RETRY: int = 5
_DEFAULT_WAIT_SEC: float = 0.2   # 200 ms — HM3301 needs time between cmd and read
_CEILING: int = 500


class AirQualityReader:
    """CircuitPython reader for the HM3301 laser PM2.5 dust sensor.

    Protocol (29-byte frame):
        Write 0x88 to 0x40  ->  triggers a reading
        Read  29 bytes       ->  raw data frame
        Byte 28              ->  CRC = sum(bytes[0:28]) & 0xFF

    Data layout (big-endian 16-bit values):
        bytes [4:6]   PM1.0 standard    ug/m3
        bytes [6:8]   PM2.5 standard    ug/m3
        bytes [8:10]  PM10  standard    ug/m3
        bytes [10:12] PM1.0 atmospheric ug/m3
        bytes [12:14] PM2.5 atmospheric ug/m3
        bytes [14:16] PM10  atmospheric ug/m3
    """

    def __init__(
        self,
        i2c,
        retry: int = _DEFAULT_RETRY,
        wait_sec: float = _DEFAULT_WAIT_SEC,
    ) -> None:
        """
        Args:
            i2c:      busio.I2C instance configured at <= 20 kHz
            retry:    max attempts before raising ValueError
            wait_sec: delay between write and read (seconds)
        """
        self._device = I2CDevice(i2c, _I2C_ADDR)
        self._retry = retry
        self._wait_sec = wait_sec

    def read(self) -> AirQualityData:
        """Poll the sensor and return a validated AirQualityData reading.

        Raises:
            ValueError: if all retry attempts fail CRC validation
        """
        buf = bytearray(_DATA_LEN)

        for attempt in range(self._retry):
            with self._device as dev:
                dev.write(_SELECT_CMD)

            time.sleep(self._wait_sec)

            with self._device as dev:
                dev.readinto(buf)

            crc = sum(buf[: _DATA_LEN - 1]) & 0xFF
            if crc != buf[28]:
                print(f"AirQuality CRC failed (attempt {attempt + 1}/{self._retry})")
                continue

            data = AirQualityData()
            data.pm_1_0_std = buf[4] << 8 | buf[5]
            data.pm_2_5_std = buf[6] << 8 | buf[7]
            data.pm_10_std = buf[8] << 8 | buf[9]
            data.pm_1_0_atm = buf[10] << 8 | buf[11]
            data.pm_2_5_atm = buf[12] << 8 | buf[13]
            data.pm_10_atm = buf[14] << 8 | buf[15]

            if data.is_high(_CEILING) and attempt < self._retry - 1:
                print(
                    f"AirQuality out-of-range values"
                    f" (attempt {attempt + 1}/{self._retry}), retrying..."
                )
                retry_data = self._read_retry(buf)
                if retry_data is not None:
                    data.clamp_to_lower(retry_data, _CEILING)

            return data

        raise ValueError(f"AirQuality: CRC failed after {self._retry} attempts")

    def _read_retry(self, buf: bytearray) -> "AirQualityData | None":
        """Do one additional read for out-of-range clamping."""
        with self._device as dev:
            dev.write(_SELECT_CMD)
        time.sleep(self._wait_sec)
        with self._device as dev:
            dev.readinto(buf)

        crc = sum(buf[: _DATA_LEN - 1]) & 0xFF
        if crc != buf[28]:
            return None

        data = AirQualityData()
        data.pm_1_0_std = buf[4] << 8 | buf[5]
        data.pm_2_5_std = buf[6] << 8 | buf[7]
        data.pm_10_std = buf[8] << 8 | buf[9]
        data.pm_1_0_atm = buf[10] << 8 | buf[11]
        data.pm_2_5_atm = buf[12] << 8 | buf[13]
        data.pm_10_atm = buf[14] << 8 | buf[15]
        return data
