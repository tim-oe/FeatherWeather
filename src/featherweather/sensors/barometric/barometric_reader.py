"""Barometric pressure and temperature reader for CircuitPython (BMP390).

Reference: https://www.adafruit.com/product/4816
Guide: https://learn.adafruit.com/adafruit-bmp388-bmp390-bmp3xx

Raw pressure is a direct physical measurement unaffected by any sea-level
setting. Altitude is NOT derived here — GPS altitude is more accurate and
is combined with raw pressure to compute sea-level normalized pressure (SLP):

    SLP = pressure / (1 - altitude_m / 44330) ** 5.255

Usage:
    import busio, board
    i2c = busio.I2C(board.SCL, board.SDA)
    reader = BarometricReader(i2c)
    data = reader.read()
    data.sea_level_pressure = BarometricReader.sea_level_pressure_from_altitude(
        data.pressure, gps_altitude_m
    )
"""

import adafruit_bmp3xx

from featherweather.sensors.barometric.barometric_data import BarometricData

__all__ = ["BarometricReader"]

_I2C_ADDR: int = 0x77

_BARO_EXPONENT: float = 5.255
_BARO_SCALE: float = 44330.0


class BarometricReader:
    """CircuitPython reader for the BMP390 barometric pressure sensor.

    Reads raw pressure (hPa) and temperature (°C). Altitude is intentionally
    omitted — use GPS altitude to compute sea-level pressure instead via
    sea_level_pressure_from_altitude().
    """

    def __init__(self, i2c, address: int = _I2C_ADDR) -> None:
        """
        Args:
            i2c:     busio.I2C instance (standard speed, e.g. 100 kHz)
            address: I2C address (0x77 default, 0x76 if SDO pulled low)
        """
        self._sensor = adafruit_bmp3xx.BMP3XX_I2C(i2c, address=address)

    def read(self) -> BarometricData:
        """Read raw pressure and temperature from the BMP390.

        Returns:
            BarometricData with pressure and temperature populated.
            sea_level_pressure is None until enriched with GPS altitude.
        """
        data = BarometricData()
        data.pressure = self._sensor.pressure
        data.temperature = self._sensor.temperature
        return data

    @staticmethod
    def sea_level_pressure_from_altitude(
        pressure_hpa: float, altitude_m: float
    ) -> float:
        """Compute sea-level normalized pressure from raw pressure and GPS altitude.

        Args:
            pressure_hpa: raw pressure reading from BMP390 (hPa)
            altitude_m:   GPS altitude above mean sea level (meters)

        Returns:
            Sea-level normalized pressure in hPa
        """
        return pressure_hpa / (1.0 - altitude_m / _BARO_SCALE) ** _BARO_EXPONENT
