"""Air quality reader for CircuitPython (HM3301).

No Adafruit library exists for this sensor — uses raw I2C via adafruit_bus_device.
Implements CRC validation and retry logic from WeatherWatch.

The HM3301 is a notoriously finicky I2C device. Two things matter for
reliable, crc-clean reads:

1) I2C bus speed. Seeed explicitly require the bus be slowed to 20 kHz.
   The default 100 kHz/400 kHz clock overruns the sensor and produces
   corrupt frames / crc failures. Set the ``I2C_FREQ_HZ`` environment
   variable to 20000 (the default for ``featherweather.hardware.i2c_bus``).

2) Read sequencing. The 0x88 "select" byte is a one-time UART->I2C mode
   switch; the reference driver issues it once at startup and then simply
   reads 29 bytes per poll. Re-issuing select before every read adds bus
   churn while the sensor is mid measurement, which provokes more corrupt
   frames. We therefore warm the sensor up once and then retry the read on
   a short, sensor-cadence backoff (the device latches a fresh frame roughly
   once per second).

Ported from https://github.com/tim-oe/WeatherWatch
Reference: https://wiki.seeedstudio.com/Grove-Laser_PM2.5_Sensor-HM3301/

Usage:
    reader = AirQualityReader()
    reader.read(payload)           # populates payload.air_quality
    # or for diagnostics:
    data = AirQualityReader.verify()
"""

import time

from adafruit_bus_device.i2c_device import I2CDevice

from featherweather.sensors.air_quality.air_quality_data import AirQualityData
from featherweather.sensors.i2c_sensor_base import I2cSensorBase
from featherweather.storage.weather_payload import WeatherPayload

__all__ = ["AirQualityReader"]

_SELECT_CMD: bytes = bytes([0x88])
_DATA_LEN: int = 29
_DEFAULT_RETRY: int = 5
_DEFAULT_WAIT_SEC: float = 10.0
_RETRY_WAIT_SEC: float = 1.0
_CEILING: int = 500


class AirQualityReader(I2cSensorBase):
    """CircuitPython reader for the HM3301 laser PM2.5 dust sensor.

    I2C address: 0x40 (fixed).

    Protocol (29-byte frame):
        Write 0x88 to 0x40 once  ->  switch sensor to I2C output mode
        Read  29 bytes per poll  ->  raw data frame
        Byte 28                  ->  CRC = sum(bytes[0:28]) & 0xFF
    """

    _I2C_ADDR: int = 0x40
    _LABEL: str = "HM3301 (Air Quality)"
    _VERIFY_WARMUP_S: float = 3.0

    def __init__(
        self,
        address: int | None = None,
        retry: int = _DEFAULT_RETRY,
        wait_sec: float = _DEFAULT_WAIT_SEC,
    ) -> None:
        self._retry = retry
        self._wait_sec = wait_sec
        # the 0x88 select / stabilization only needs to happen once for the
        # life of the process; tracked here so repeated reads don't re-toggle
        # the sensor mode on every poll
        self._warmed_up: bool = False
        super().__init__(address)

    def _init_device(self, i2c, address: int) -> None:
        self._device = I2CDevice(i2c, address)

    def read(self, payload: WeatherPayload) -> None:
        """Poll the HM3301, validate CRC, and set payload.air_quality.

        Args:
            payload: in-progress WeatherPayload; payload.air_quality is written.

        Raises:
            ValueError: if all retry attempts fail CRC validation.
        """
        data = self._read_validated()
        print(data)
        payload.air_quality = data

    @classmethod
    def verify(cls) -> "AirQualityData":
        """Wait for sensor warmup, instantiate, take one reading, and return data.

        Raises on any hardware or communication failure.
        """
        time.sleep(cls._VERIFY_WARMUP_S)
        sensor = cls(retry=8)
        payload = WeatherPayload()
        sensor.read(payload)
        return payload.air_quality

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _warm_up(self) -> None:
        """Switch the sensor to I2C output mode and let it stabilize.

        This is a one-time operation; the 0x88 select byte is a UART->I2C
        mode switch and the datasheet calls for settle time after power-on
        before readings are trustworthy.
        """
        if self._warmed_up:
            return

        with self._device as dev:
            dev.write(_SELECT_CMD)
        time.sleep(self._wait_sec)
        self._warmed_up = True

    def _read_validated(self) -> AirQualityData:
        """Run the CRC-validated read loop and return AirQualityData."""
        self._warm_up()
        buf = bytearray(_DATA_LEN)

        for attempt in range(self._retry):
            try:
                self._read_frame(buf)
            except OSError as exc:
                print(
                    f"AirQuality i2c read failed "
                    f"(attempt {attempt + 1}/{self._retry}): {exc}"
                )
                time.sleep(_RETRY_WAIT_SEC)
                continue

            if not self._crc_valid(buf):
                print(f"AirQuality CRC failed (attempt {attempt + 1}/{self._retry})")
                time.sleep(_RETRY_WAIT_SEC)
                continue

            data = self._parse(buf)

            if data.is_high(_CEILING) and attempt < self._retry - 1:
                print(
                    f"AirQuality out-of-range "
                    f"(attempt {attempt + 1}/{self._retry}), retrying..."
                )
                retry_data = self._read_one(buf)
                if retry_data is not None:
                    data.clamp_to_lower(retry_data, _CEILING)

            return data

        raise ValueError(f"AirQuality: CRC failed after {self._retry} attempts")

    def _read_frame(self, buf: bytearray) -> None:
        """Read one 29-byte frame from the sensor."""
        with self._device as dev:
            dev.readinto(buf)

    def _read_one(self, buf: bytearray) -> "AirQualityData | None":
        """Single extra read for out-of-range clamping (no re-select)."""
        try:
            self._read_frame(buf)
        except OSError:
            return None
        if not self._crc_valid(buf):
            return None
        return self._parse(buf)

    @staticmethod
    def _crc_valid(buf: bytearray) -> bool:
        """Return True when the trailing checksum byte matches the frame sum."""
        if len(buf) != _DATA_LEN:
            return False
        crc = sum(buf[: _DATA_LEN - 1]) & 0xFF
        return crc == buf[_DATA_LEN - 1]

    @staticmethod
    def _parse(buf: bytearray) -> AirQualityData:
        data = AirQualityData()
        data.pm_1_0_std = buf[4] << 8 | buf[5]
        data.pm_2_5_std = buf[6] << 8 | buf[7]
        data.pm_10_std = buf[8] << 8 | buf[9]
        data.pm_1_0_atm = buf[10] << 8 | buf[11]
        data.pm_2_5_atm = buf[12] << 8 | buf[13]
        data.pm_10_atm = buf[14] << 8 | buf[15]
        return data
