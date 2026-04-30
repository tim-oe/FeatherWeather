"""Ambient light / illuminance reader for CircuitPython (SEN0644).

Modbus RTU registers (default slave address 0x01):
    0x0002  UINT16 RO  illumination high 16 bits  combine to
    0x0003  UINT16 RO  illumination low  16 bits  32-bit uint / 1000 = lux
    0x0064  UINT16 RW  device address (1-254)

Reference: https://wiki.dfrobot.com/sen0644/docs/19609

Usage:
    import busio, board, digitalio
    uart = busio.UART(board.TX, board.RX, baudrate=9600, timeout=0.5)
    de = digitalio.DigitalInOut(board.D5)
    de.direction = digitalio.Direction.OUTPUT
    reader = IlluminanceReader(uart, de)
    data = reader.read()
"""

from featherweather.sensors.illuminance.illuminance_data import IlluminanceData
from featherweather.sensors.rs485.modbus_rtu import ModbusRtu

__all__ = ["IlluminanceReader"]

_DEFAULT_ADDRESS: int = 0x01
_REG_LUX_HIGH: int = 0x0002
_REG_DEVICE_ADDR: int = 0x0064

_LUX_SCALE: float = 1000.0


class IlluminanceReader:
    """CircuitPython reader for the SEN0644 RS485 ambient light sensor.

    The illuminance value is a 32-bit integer split across two consecutive
    16-bit registers (high word at 0x0002, low word at 0x0003).
    Divide the combined value by 1000 to get lux.
    """

    def __init__(self, uart, de_pin, address: int = _DEFAULT_ADDRESS) -> None:
        """
        Args:
            uart:    busio.UART at 9600 8N1 connected to MAX3485
            de_pin:  digitalio.DigitalInOut for MAX3485 DE / ~RE
            address: Modbus slave address (default 0x01)
        """
        self._modbus = ModbusRtu(uart, de_pin)
        self._address = address

    def read(self) -> IlluminanceData:
        """Read ambient illuminance from the SEN0644.

        Returns:
            IlluminanceData with lux value (0.0 - 200,000.0)
        """
        regs = self._modbus.read_registers(self._address, _REG_LUX_HIGH, count=2)
        raw = (regs[0] << 16) | regs[1]
        data = IlluminanceData()
        data.lux = raw / _LUX_SCALE
        return data

    def set_address(self, new_address: int) -> None:
        """Reassign the Modbus slave address (persisted in sensor flash).

        Power-cycle the sensor after calling this method.
        """
        self._modbus.write_register(self._address, _REG_DEVICE_ADDR, new_address)
        self._address = new_address
