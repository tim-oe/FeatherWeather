"""Minimal Modbus RTU master for CircuitPython over UART + MAX3485.

Supports Function Code 0x03 (Read Holding Registers) and
Function Code 0x06 (Write Single Register) — sufficient for all
DFRobot RS485 weather sensors.

The MAX3485 is half-duplex. A single GPIO (de_pin) controls the direction:
    HIGH → transmit (Driver Enable)
    LOW  → receive  (Receiver Enable via /RE tied to DE)
"""

import time

__all__ = ["ModbusRtu"]

_BAUD_RATE: int = 9600
_FUNC_READ: int = 0x03
_FUNC_WRITE_SINGLE: int = 0x06

# At 9600 baud: 1 char = 10 bits = 1.042 ms; 3.5-char inter-frame gap ≈ 3.7 ms
_CHAR_TIME_S: float = 10 / _BAUD_RATE
_INTERFRAME_S: float = 3.5 * _CHAR_TIME_S + 0.002  # add 2 ms margin
_TX_SETTLING_S: float = 0.002  # DE hold time after last byte


class ModbusRtu:
    """Half-duplex Modbus RTU master for RS485 sensors via MAX3485.

    Args:
        uart:     busio.UART configured at 9600 8N1 (timeout >= 0.5 s)
        de_pin:   digitalio.DigitalInOut driving MAX3485 DE / ~RE
        timeout:  read timeout in seconds (default 0.5)
    """

    def __init__(self, uart, de_pin, timeout: float = 0.5) -> None:
        self._uart = uart
        self._de = de_pin
        self._timeout = timeout
        self._de.value = False  # start in receive mode

    def read_registers(self, address: int, register: int, count: int) -> list:
        """Send FC 0x03 and return a list of register values (16-bit ints).

        Args:
            address:  Modbus slave address (1-247)
            register: starting register address
            count:    number of 16-bit registers to read

        Returns:
            List of `count` unsigned 16-bit integers

        Raises:
            ValueError: on CRC mismatch, short response, or error response
        """
        frame = self._build_frame(
            address,
            _FUNC_READ,
            register >> 8,
            register & 0xFF,
            count >> 8,
            count & 0xFF,
        )
        self._transmit(frame)
        raw = self._receive(3 + count * 2 + 2)  # header + data + CRC
        self._validate_crc(raw)
        if raw[1] & 0x80:
            raise ValueError(f"Modbus exception code {raw[2]:#04x}")
        return [(raw[3 + i * 2] << 8) | raw[4 + i * 2] for i in range(count)]

    def write_register(self, address: int, register: int, value: int) -> None:
        """Send FC 0x06 to write a single 16-bit register value.

        Args:
            address:  Modbus slave address
            register: register address
            value:    16-bit value to write
        """
        frame = self._build_frame(
            address,
            _FUNC_WRITE_SINGLE,
            register >> 8,
            register & 0xFF,
            value >> 8,
            value & 0xFF,
        )
        self._transmit(frame)
        self._receive(8)  # echo frame, discard

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_frame(self, *bytes_) -> bytearray:
        frame = bytearray(bytes_)
        crc = self._crc16(frame)
        frame += bytearray([crc & 0xFF, crc >> 8])
        return frame

    def _transmit(self, frame: bytearray) -> None:
        """Toggle DE high, write frame, wait for TX complete, then go low."""
        self._de.value = True
        self._uart.write(frame)
        time.sleep(len(frame) * _CHAR_TIME_S + _TX_SETTLING_S)
        self._de.value = False
        time.sleep(_INTERFRAME_S)

    def _receive(self, expected_len: int) -> bytearray:
        """Read expected_len bytes with timeout."""
        buf = bytearray()
        deadline = time.monotonic() + self._timeout
        while len(buf) < expected_len and time.monotonic() < deadline:
            chunk = self._uart.read(expected_len - len(buf))
            if chunk:
                buf += chunk
        if len(buf) < expected_len:
            raise ValueError(f"Modbus timeout: got {len(buf)}/{expected_len} bytes")
        return buf

    def _validate_crc(self, frame: bytearray) -> None:
        computed = self._crc16(frame[:-2])
        received = frame[-2] | (frame[-1] << 8)
        if computed != received:
            raise ValueError(f"Modbus CRC mismatch: {computed:#06x} != {received:#06x}")

    @staticmethod
    def _crc16(data: bytearray) -> int:
        """Standard Modbus CRC16."""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc
