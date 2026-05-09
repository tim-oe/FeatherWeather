"""Base class for all I2C sensor readers in FeatherWeather.

Subclasses declare a class-level ``_I2C_ADDR`` and implement
``_init_device(i2c, address)`` to wire up the hardware driver.  The
shared I2C bus is obtained automatically from
``featherweather.hardware.i2c_bus`` so callers never need to pass a
bus object.

Typical subclass pattern::

    class MyReader(I2cSensorBase):
        _I2C_ADDR = 0x42

        def _init_device(self, i2c, address: int) -> None:
            self._sensor = SomeAdafruitDriver(i2c, address=address)

        def read(self, payload) -> None:
            data = MyData(...)
            payload.my_field = data

        @classmethod
        def verify(cls) -> \"MyData\":
            from featherweather.storage.weather_payload import WeatherPayload
            sensor = cls()
            payload = WeatherPayload()
            sensor.read(payload)
            return payload.my_field
"""

from featherweather.hardware.i2c_bus import get_i2c
from featherweather.sensors.sensor_base import SensorBase

__all__ = ["I2cSensorBase"]


class I2cSensorBase(SensorBase):
    """Common base for every sensor on the shared I2C bus.

    Inherits the ``read(payload)`` contract from ``SensorBase``.

    Class attributes to set in subclasses:
        _I2C_ADDR  (int): default device address used when none is passed
                          to the constructor.
        _LABEL     (str): human-readable device name shown on the I2C bus scan
                          (e.g. ``"BMP390 (Barometric)"``).
    """

    _I2C_ADDR: int = 0x00
    _LABEL: str = ""

    def __init__(self, address: int | None = None) -> None:
        """Obtain the shared I2C bus and delegate to ``_init_device``.

        Args:
            address: I2C address override; falls back to ``_I2C_ADDR``.
        """
        addr = address if address is not None else self._I2C_ADDR
        self._init_device(get_i2c(), addr)

    def _init_device(self, i2c, address: int) -> None:
        """Initialise the hardware driver.  Must be overridden in every subclass."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement _init_device()"
        )
