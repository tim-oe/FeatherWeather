"""Base class for all RS485 / Modbus RTU sensor readers in FeatherWeather.

Subclasses declare ``_DEFAULT_ADDRESS`` and optionally ``_ENV_ADDRESS_VAR``
(the settings.toml key that overrides the address at runtime), then rely on
``super().__init__()`` to wire up the shared RS485 bus and a ready-to-use
``ModbusRtu`` instance.

Typical subclass pattern::

    class MyReader(Rs485SensorBase):
        _DEFAULT_ADDRESS = 0x02
        _ENV_ADDRESS_VAR = \"MY_SENSOR_ADDR\"

        def read(self) -> MyData:
            regs = self._modbus.read_registers(self._address, 0x0000, count=1)
            ...

Address resolution order (first match wins):
    1. ``address`` constructor argument
    2. ``os.getenv(_ENV_ADDRESS_VAR)`` when ``_ENV_ADDRESS_VAR`` is set
    3. ``_DEFAULT_ADDRESS``
"""

import os

from featherweather.hardware.rs485_bus import get_rs485
from featherweather.sensors.rs485.modbus_rtu import ModbusRtu
from featherweather.sensors.sensor_base import SensorBase

__all__ = ["Rs485SensorBase"]


class Rs485SensorBase(SensorBase):
    """Common base for all Modbus RTU sensors on the shared RS485 bus.

    Inherits the ``read(payload)`` contract from ``SensorBase``.

    Class attributes to override in subclasses:
        _DEFAULT_ADDRESS (int): Modbus slave address used when no override exists.
        _ENV_ADDRESS_VAR (str): settings.toml key for address override;
                                empty string disables env-var lookup.
    """

    _DEFAULT_ADDRESS: int = 0x01
    _ENV_ADDRESS_VAR: str = ""

    def __init__(self, address: int | None = None) -> None:
        """Resolve the Modbus address and acquire the shared RS485 bus.

        Args:
            address: explicit Modbus slave address; skips env-var and default
                     lookup when provided.

        Raises:
            Any exception raised by ``get_rs485()`` (e.g. UART unavailable)
            so that ``_try_init()`` in code.py can skip the sensor cleanly.
        """
        if address is None and self._ENV_ADDRESS_VAR:
            raw = os.getenv(self._ENV_ADDRESS_VAR)
            if raw:
                address = int(raw, 0)  # accept 0x-prefixed hex or plain decimal
        self._address = address if address is not None else self._DEFAULT_ADDRESS
        uart, de_pin = get_rs485()
        self._modbus = ModbusRtu(uart, de_pin)
