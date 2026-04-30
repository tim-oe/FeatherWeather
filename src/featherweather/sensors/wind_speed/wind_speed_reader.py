"""Wind speed reader for CircuitPython (SEN0483).

Modbus RTU registers (default slave address 0x02):
    0x0000  INT16 RO  wind speed: raw / 10 = m/s (0.0 - 32.4)
    0x1000  INT16 RW  Modbus slave address (0-255)

Reference: https://wiki.dfrobot.com/sen0483/docs/20112

Usage:
    import busio, board, digitalio
    uart = busio.UART(board.TX, board.RX, baudrate=9600, timeout=0.5)
    de = digitalio.DigitalInOut(board.D5)
    de.direction = digitalio.Direction.OUTPUT
    reader = WindSpeedReader(uart, de)
    data = reader.read()
"""

from featherweather.sensors.rs485.modbus_rtu import ModbusRtu
from featherweather.sensors.wind_speed.wind_speed_data import (
    WindSpeedData,
    beaufort_description,
)

__all__ = ["WindSpeedReader"]

_DEFAULT_ADDRESS: int = 0x02
_REG_WIND_SPEED: int = 0x0000
_REG_MODBUS_ADDRESS: int = 0x1000

_SPEED_SCALE: float = 10.0


class WindSpeedReader:
    """CircuitPython reader for the SEN0483 RS485 wind speed sensor."""

    def __init__(self, uart, de_pin, address: int = _DEFAULT_ADDRESS) -> None:
        """
        Args:
            uart:    busio.UART at 9600 8N1 connected to MAX3485
            de_pin:  digitalio.DigitalInOut for MAX3485 DE / ~RE
            address: Modbus slave address (default 0x02)
        """
        self._modbus = ModbusRtu(uart, de_pin)
        self._address = address

    def read(self) -> WindSpeedData:
        """Read wind speed from the SEN0483.

        Returns:
            WindSpeedData with speed_ms and Beaufort description
        """
        regs = self._modbus.read_registers(self._address, _REG_WIND_SPEED, count=1)
        data = WindSpeedData()
        data.speed_ms = regs[0] / _SPEED_SCALE
        data.beaufort = beaufort_description(data.speed_ms)
        return data

    def set_address(self, new_address: int) -> None:
        """Reassign the Modbus slave address (persisted in sensor flash).

        Run with the sensor connected alone on the bus, then power-cycle.
        """
        self._modbus.write_register(self._address, _REG_MODBUS_ADDRESS, new_address)
        self._address = new_address
