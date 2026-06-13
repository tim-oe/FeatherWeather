"""Wind speed reader for CircuitPython (SEN0483).

Modbus RTU registers (default slave address 0x03; reprogrammed from default 0x02
to avoid conflict with the SEN0482 wind direction sensor):
    0x0000  INT16 RO  wind speed: raw / 10 = m/s (0.0 - 32.4)
    0x1000  INT16 RW  Modbus slave address (0-255)

Reference: https://wiki.dfrobot.com/sen0483/docs/20112

Environment variable:
    WIND_SPD_ADDR  Modbus slave address, hex or decimal (default 0x03)

Usage:
    reader = WindSpeedReader()
    reader.read(payload)           # populates payload.wind_speed
    # or for diagnostics:
    data = WindSpeedReader.verify()
"""

from featherweather.sensors.rs485.rs485_sensor_base import Rs485SensorBase
from featherweather.sensors.wind_speed.wind_speed_data import (
    WindSpeedData,
    beaufort_description,
)
from featherweather.storage.weather_payload import WeatherPayload

__all__ = ["WindSpeedReader"]

_REG_WIND_SPEED: int = 0x0000
_REG_MODBUS_ADDRESS: int = 0x1000

_SPEED_SCALE: float = 10.0


class WindSpeedReader(Rs485SensorBase):
    """CircuitPython reader for the SEN0483 RS485 wind speed sensor.

    Modbus address defaults to 0x03 (reprogrammed from factory default 0x02).
    Override with ``WIND_SPD_ADDR`` or the ``address`` constructor argument.
    """

    _DEFAULT_ADDRESS: int = 0x03
    _ENV_ADDRESS_VAR: str = "WIND_SPD_ADDR"

    def read(self, payload: WeatherPayload) -> None:
        """Read wind speed from the SEN0483 and set payload.wind_speed.

        Args:
            payload: in-progress WeatherPayload; payload.wind_speed is written.
        """
        regs = self._modbus.read_registers(self._address, _REG_WIND_SPEED, count=1)
        data = WindSpeedData()
        data.speed_ms = regs[0] / _SPEED_SCALE
        data.beaufort = beaufort_description(data.speed_ms)
        print(data)
        payload.wind_speed = data

    @classmethod
    def verify(cls) -> "WindSpeedData":
        """Instantiate, take one reading, and return the WindSpeedData.

        Raises on any hardware or communication failure.
        """
        from featherweather.storage.weather_payload import (  # noqa: PLC0415
            WeatherPayload,
        )

        sensor = cls()
        payload = WeatherPayload()
        sensor.read(payload)
        return payload.wind_speed

    def set_address(self, new_address: int) -> None:
        """Reassign the Modbus slave address (persisted in sensor flash)."""
        self._modbus.write_register(self._address, _REG_MODBUS_ADDRESS, new_address)
        self._address = new_address
