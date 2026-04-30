"""Wind direction reader for CircuitPython (SEN0482).

Modbus RTU registers (default slave address 0x02):
    0x0000  INT16 RO  360° direction: raw / 10 = degrees (0.0 - 360.0)
    0x0001  INT16 RO  16-direction code: 0 (N) ... 15 (NNW), clockwise
    0x1000  INT16 RW  Modbus slave address (0-255)

Reference: https://wiki.dfrobot.com/sen0482/docs/19708

Usage:
    import busio, board, digitalio
    uart = busio.UART(board.TX, board.RX, baudrate=9600, timeout=0.5)
    de = digitalio.DigitalInOut(board.D5)
    de.direction = digitalio.Direction.OUTPUT
    reader = WindDirectionReader(uart, de)
    data = reader.read()
"""

from featherweather.sensors.rs485.modbus_rtu import ModbusRtu
from featherweather.sensors.wind_direction.wind_direction_data import (
    DIRECTION_LABELS,
    WindDirectionData,
)

__all__ = ["WindDirectionReader"]

_DEFAULT_ADDRESS: int = 0x02
_REG_DEGREES: int = 0x0000
_REG_MODBUS_ADDRESS: int = 0x1000

_DEGREES_SCALE: float = 10.0


class WindDirectionReader:
    """CircuitPython reader for the SEN0482 RS485 wind vane sensor.

    Reads both the continuous 360° angle and the 16-point compass code
    in a single Modbus transaction (two consecutive registers).
    """

    def __init__(self, uart, de_pin, address: int = _DEFAULT_ADDRESS) -> None:
        """
        Args:
            uart:    busio.UART at 9600 8N1 connected to MAX3485
            de_pin:  digitalio.DigitalInOut for MAX3485 DE / ~RE
            address: Modbus slave address (default 0x02)
        """
        self._modbus = ModbusRtu(uart, de_pin)
        self._address = address

    def read(self) -> WindDirectionData:
        """Read wind direction from the SEN0482.

        Returns:
            WindDirectionData with degrees, direction_code, and direction_label
        """
        regs = self._modbus.read_registers(self._address, _REG_DEGREES, count=2)
        data = WindDirectionData()
        data.degrees = regs[0] / _DEGREES_SCALE
        data.direction_code = regs[1]
        code = min(regs[1], len(DIRECTION_LABELS) - 1)
        data.direction_label = DIRECTION_LABELS[code]
        return data

    def set_address(self, new_address: int) -> None:
        """Reassign the Modbus slave address (persisted in sensor flash).

        Run with the sensor connected alone on the bus, then power-cycle.
        """
        self._modbus.write_register(self._address, _REG_MODBUS_ADDRESS, new_address)
        self._address = new_address
