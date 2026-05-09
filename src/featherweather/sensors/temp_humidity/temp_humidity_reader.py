"""Temperature and humidity reader for CircuitPython (SHTC3).

Reference: https://www.adafruit.com/product/4636
Guide: https://learn.adafruit.com/adafruit-sensirion-shtc3-temperature-humidity-sensor

Usage:
    import busio, board
    i2c = busio.I2C(board.SCL, board.SDA)
    reader = TempHumidityReader(i2c)
    data = reader.read()
"""

import adafruit_shtc3

from featherweather.sensors.temp_humidity.temp_humidity_data import TempHumidityData

__all__ = ["TempHumidityReader"]


class TempHumidityReader:
    """CircuitPython reader for the SHTC3 temperature and humidity sensor.

    Fixed I2C address 0x70. Both values are fetched atomically via
    sensor.measurements to avoid a race between two separate reads.
    """

    def __init__(self, i2c) -> None:
        """
        Args:
            i2c: busio.I2C instance (standard speed, e.g. 100 kHz)
        """
        self._sensor = adafruit_shtc3.SHTC3(i2c)

    @classmethod
    def verify(cls, i2c) -> "TempHumidityData":
        """Instantiate, take one reading, and return the data.

        Raises on any hardware or communication failure.
        """
        return cls(i2c).read()

    def read(self) -> TempHumidityData:
        """Read temperature and relative humidity from the SHTC3.

        Returns:
            TempHumidityData populated with the current sensor values
        """
        temperature, relative_humidity = self._sensor.measurements
        data = TempHumidityData()
        data.temperature = temperature
        data.relative_humidity = relative_humidity
        return data
