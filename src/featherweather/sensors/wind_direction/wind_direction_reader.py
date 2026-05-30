"""Wind direction reader for CircuitPython (SEN0482).

Modbus RTU registers (default slave address 0x02):
    0x0000  INT16 RO  360° direction: raw / 10 = degrees (0.0 - 360.0)
    0x0001  INT16 RO  16-direction code: 0 (N) ... 15 (NNW), clockwise
    0x1000  INT16 RW  Modbus slave address (0-255)

Reference: https://wiki.dfrobot.com/sen0482/docs/19708

Environment variable:
    WIND_DIR_ADDR  Modbus slave address, hex or decimal (default 0x02)

Usage:
    reader = WindDirectionReader()
    reader.read(payload)           # populates payload.wind_direction
    # or for diagnostics:
    data = WindDirectionReader.verify()
"""

from featherweather.sensors.rs485.rs485_sensor_base import Rs485SensorBase
from featherweather.sensors.wind_direction.wind_direction_data import (
    DIRECTION_LABELS,
    WindDirectionData,
)
from featherweather.storage.weather_payload import WeatherPayload

__all__ = ["WindDirectionReader"]

_REG_DEGREES: int = 0x0000
_REG_MODBUS_ADDRESS: int = 0x1000

_DEGREES_SCALE: float = 10.0


class WindDirectionReader(Rs485SensorBase):
    """CircuitPython reader for the SEN0482 RS485 wind vane sensor.

    Reads both the continuous 360° angle and the 16-point compass code
    in a single Modbus transaction (two consecutive registers).

    Modbus address defaults to 0x02; override with ``WIND_DIR_ADDR`` or
    the ``address`` constructor argument.
    """

    _DEFAULT_ADDRESS: int = 0x02
    _ENV_ADDRESS_VAR: str = "WIND_DIR_ADDR"

    def read(self, payload: WeatherPayload) -> None:
        """Read wind direction from the SEN0482 and set payload.wind_direction.

        Args:
            payload: in-progress WeatherPayload; payload.wind_direction is written.
        """
        regs = self._modbus.read_registers(self._address, _REG_DEGREES, count=2)
        data = WindDirectionData()
        data.degrees = regs[0] / _DEGREES_SCALE
        data.direction_code = regs[1]
        code = min(regs[1], len(DIRECTION_LABELS) - 1)
        data.direction_label = DIRECTION_LABELS[code]
        print(data)
        payload.wind_direction = data

    @classmethod
    def verify(cls) -> "WindDirectionData":
        """Instantiate, take one reading, and return the WindDirectionData.

        Raises on any hardware or communication failure.
        """
        from featherweather.storage.weather_payload import WeatherPayload  # noqa: PLC0415

        sensor = cls()
        payload = WeatherPayload()
        sensor.read(payload)
        return payload.wind_direction

    def set_address(self, new_address: int) -> None:
        """Reassign the Modbus slave address (persisted in sensor flash).

        Run with the sensor connected alone on the bus, then power-cycle.
        """
        self._modbus.write_register(self._address, _REG_MODBUS_ADDRESS, new_address)
        self._address = new_address
