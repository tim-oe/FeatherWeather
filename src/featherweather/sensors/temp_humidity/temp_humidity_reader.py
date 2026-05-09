"""Temperature and humidity reader for CircuitPython (SHTC3).

Reference: https://www.adafruit.com/product/4636
Guide: https://learn.adafruit.com/adafruit-sensirion-shtc3-temperature-humidity-sensor

The SHTC3 has a fixed I2C address (0x70) and requires no address configuration.

Usage:
    reader = TempHumidityReader()
    reader.read(payload)           # populates payload.temp_humidity
    # or for diagnostics:
    data = TempHumidityReader.verify()
"""

import adafruit_shtc3

from featherweather.sensors.i2c_sensor_base import I2cSensorBase
from featherweather.sensors.temp_humidity.temp_humidity_data import TempHumidityData
from featherweather.storage.weather_payload import WeatherPayload

__all__ = ["TempHumidityReader"]


class TempHumidityReader(I2cSensorBase):
    """CircuitPython reader for the SHTC3 temperature and humidity sensor.

    Fixed I2C address 0x70. Both values are fetched atomically via
    ``sensor.measurements`` to avoid a race between two separate reads.
    """

    _I2C_ADDR: int = 0x70
    _LABEL: str = "SHTC3 (Temp/Humidity)"

    def _init_device(self, i2c, address: int) -> None:
        self._sensor = adafruit_shtc3.SHTC3(i2c)

    def read(self, payload: WeatherPayload) -> None:
        """Read temperature and relative humidity from the SHTC3 and set
        payload.temp_humidity.

        Args:
            payload: in-progress WeatherPayload; payload.temp_humidity is written.
        """
        temperature, relative_humidity = self._sensor.measurements
        data = TempHumidityData()
        data.temperature = temperature
        data.relative_humidity = relative_humidity
        print(data)
        payload.temp_humidity = data

    @classmethod
    def verify(cls) -> "TempHumidityData":
        """Instantiate, take one reading, and return the TempHumidityData.

        Raises on any hardware or communication failure.
        """
        sensor = cls()
        payload = WeatherPayload()
        sensor.read(payload)
        return payload.temp_humidity
