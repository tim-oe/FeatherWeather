"""Common sensor interface for FeatherWeather.

Every sensor reader — whether I2C, RS485, or I2S — implements
``read(payload)`` to populate its own slice of a ``WeatherPayload``.
This keeps the sensor cycle in code.py to a simple loop::

    for sensor in sensors:
        sensor.read(payload)

Adding a new sensor therefore requires no changes to code.py or
diagnostic.py; only the sensor reader and the payload need updating.
"""

from featherweather.storage.weather_payload import WeatherPayload

__all__ = ["SensorBase"]


class SensorBase:
    """Abstract interface shared by all FeatherWeather sensor readers.

    Subclasses must implement ``read(payload)`` to pull data from hardware
    and write it into the appropriate field(s) of the supplied payload.
    They should also expose a ``verify()`` classmethod for diagnostic use.
    """

    def read(self, payload: WeatherPayload) -> None:
        """Read sensor data and populate the relevant field(s) of *payload*.

        Implementations must:
          1. Sample the hardware.
          2. Set the matching attribute on *payload*
             (e.g. ``payload.barometric = data``).
          3. Raise on unrecoverable hardware failure so callers can catch
             and skip the sensor gracefully.

        Args:
            payload: the in-progress ``WeatherPayload`` for this reading cycle.
                     Implementations may also *read* other fields on the payload
                     (e.g. ``payload.gps.altitude_m`` for sea-level pressure).
        """
        raise NotImplementedError(f"{type(self).__name__} must implement read(payload)")
