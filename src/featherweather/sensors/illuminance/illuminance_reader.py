"""Ambient light / illuminance reader for CircuitPython (SEN0644).

Modbus RTU registers (default slave address 0x01):
    0x0002  UINT16 RO  illumination high 16 bits  combine to
    0x0003  UINT16 RO  illumination low  16 bits  32-bit uint / 1000 = lux
    0x0064  UINT16 RW  device address (1-254)

Reference: https://wiki.dfrobot.com/sen0644/docs/19609

Environment variable:
    ILLUMINANCE_ADDR  Modbus slave address, hex or decimal (default 0x01)

Usage:
    reader = IlluminanceReader()
    reader.read(payload)           # populates payload.illuminance
"""

from featherweather.sensors.illuminance.illuminance_data import IlluminanceData
from featherweather.sensors.rs485.rs485_sensor_base import Rs485SensorBase
from featherweather.storage.weather_payload import WeatherPayload

__all__ = ["IlluminanceReader"]

_REG_LUX_HIGH: int = 0x0002
_REG_DEVICE_ADDR: int = 0x0064

_LUX_SCALE: float = 1000.0


class IlluminanceReader(Rs485SensorBase):
    """CircuitPython reader for the SEN0644 RS485 ambient light sensor.

    The illuminance value is a 32-bit integer split across two consecutive
    16-bit registers (high word at 0x0002, low word at 0x0003).

    Modbus address defaults to 0x01; override with ``ILLUMINANCE_ADDR`` or
    the ``address`` constructor argument.
    """

    _DEFAULT_ADDRESS: int = 0x01
    _ENV_ADDRESS_VAR: str = "ILLUMINANCE_ADDR"

    def read(self, payload: WeatherPayload) -> None:
        """Read ambient illuminance from the SEN0644 and set payload.illuminance.

        Args:
            payload: in-progress WeatherPayload; payload.illuminance is written.
        """
        regs = self._modbus.read_registers(self._address, _REG_LUX_HIGH, count=2)
        raw = (regs[0] << 16) | regs[1]
        data = IlluminanceData()
        data.lux = raw / _LUX_SCALE
        print(data)
        payload.illuminance = data

    def set_address(self, new_address: int) -> None:
        """Reassign the Modbus slave address (persisted in sensor flash)."""
        self._modbus.write_register(self._address, _REG_DEVICE_ADDR, new_address)
        self._address = new_address
