"""Barometric pressure and temperature reader for CircuitPython (BMP390).

Reference: https://www.adafruit.com/product/4816
Guide: https://learn.adafruit.com/adafruit-bmp388-bmp390-bmp3xx

Raw pressure is a direct physical measurement unaffected by any sea-level
setting. Altitude is NOT derived here — GPS altitude is consumed from the
in-progress WeatherPayload to compute sea-level normalized pressure (SLP):

    SLP = pressure / (1 - altitude_m / 44330) ** 5.255

Environment variable:
    BARO_ADDR  I2C address override, hex or decimal (default 0x77; use 0x76
               when SDO is pulled low)

Usage:
    reader = BarometricReader()
    reader.read(payload)           # populates payload.barometric
    # or for diagnostics:
    data = BarometricReader.verify()
"""

import adafruit_bmp3xx

from featherweather.sensors.barometric.barometric_data import BarometricData
from featherweather.sensors.i2c_sensor_base import I2cSensorBase
from featherweather.storage.weather_payload import WeatherPayload

__all__ = ["BarometricReader"]

_BARO_EXPONENT: float = 5.255
_BARO_SCALE: float = 44330.0


class BarometricReader(I2cSensorBase):
    """CircuitPython reader for the BMP390 barometric pressure sensor.

    I2C address: 0x77 (default) or 0x76 (SDO pulled low).  Override with the
    ``BARO_ADDR`` environment variable or the ``address`` constructor argument.
    """

    _I2C_ADDR: int = 0x77
    _LABEL: str = "BMP390 (Barometric)"

    def __init__(self, address: int | None = None) -> None:
        import os  # noqa: PLC0415

        if address is None:
            raw = os.getenv("BARO_ADDR")
            if raw:
                address = int(raw, 0)
        super().__init__(address)

    def _init_device(self, i2c, address: int) -> None:
        self._sensor = adafruit_bmp3xx.BMP3XX_I2C(i2c, address=address)

    def read(self, payload: WeatherPayload) -> None:
        """Read pressure and temperature from the BMP390 and set payload.barometric.

        Also computes sea-level pressure when ``payload.gps.altitude_m`` is
        available, so GPS data from the same cycle is automatically incorporated.

        Args:
            payload: in-progress WeatherPayload; payload.gps is read for
                     altitude, payload.barometric is written.
        """
        data = BarometricData()
        data.pressure = self._sensor.pressure
        data.temperature = self._sensor.temperature

        gps = payload.gps
        if gps is not None and gps.altitude_m is not None:
            data.sea_level_pressure = self.sea_level_pressure_from_altitude(
                data.pressure, gps.altitude_m
            )

        print(data)
        payload.barometric = data

    @classmethod
    def verify(cls) -> "BarometricData":
        """Instantiate, take one reading, and return the BarometricData.

        Raises on any hardware or communication failure.
        """
        sensor = cls()
        payload = WeatherPayload()
        sensor.read(payload)
        return payload.barometric

    @staticmethod
    def sea_level_pressure_from_altitude(
        pressure_hpa: float, altitude_m: float
    ) -> float:
        """Compute sea-level normalized pressure from raw pressure and GPS altitude."""
        return pressure_hpa / (1.0 - altitude_m / _BARO_SCALE) ** _BARO_EXPONENT
