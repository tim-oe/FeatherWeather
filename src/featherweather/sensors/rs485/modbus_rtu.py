"""Minimal Modbus RTU master for CircuitPython over UART.

Supports Function Code 0x03 (Read Holding Registers) and
Function Code 0x06 (Write Single Register) — sufficient for all
DFRobot RS485 weather sensors.

RS485 adapter: DFRobot DFR0845 (active isolated, auto-direction).
When de_pin is None (RS485_AUTO_DIRECTION = true) the adapter handles
TX/RX switching internally. When de_pin is set, it is driven HIGH to
transmit and LOW to receive (for manual DE/~RE adapters).
"""

import os
import time

__all__ = ["ModbusRtu"]

_BAUD_RATE: int = 9600
_FUNC_READ: int = 0x03
_FUNC_WRITE_SINGLE: int = 0x06

# At 9600 baud: 1 char = 10 bits = 1.042 ms; 3.5-char inter-frame gap ≈ 3.7 ms
_TX_SETTLING_S: float = 0.002  # DE hold time after last byte (or last UART bit)
# Auto-direction TTL boards often need extra ms after TX before the RO pin listens.
_AUTO_DIR_EXTRA_S: float = 0.015


def _inter_request_delay_s() -> float:
    """Optional silence before each new Modbus frame (multi-drop / picky slaves)."""
    raw = os.getenv("RS485_INTER_REQUEST_MS")
    if raw is None or not str(raw).strip():
        return 0.0
    return max(0, int(str(raw).strip(), 0)) / 1000.0


# Last Modbus unit ID that finished a transaction — shared across all ModbusRtu
# instances so wind_dir (0x02) -> wind_spd (0x03) on separate readers still gaps.
_LAST_MODBUS_SLAVE: int | None = None


def _slave_switch_extra_s(next_addr: int) -> float:
    """Extra delay when the next request targets a different slave than the last."""
    raw = os.getenv("RS485_SLAVE_SWITCH_MS")
    if raw is not None and str(raw).strip().lower() in ("0", "false", "no", "off"):
        return 0.0
    ms = 80
    if raw is not None and str(raw).strip():
        ms = int(str(raw).strip(), 0)
    if ms <= 0:
        return 0.0
    if _LAST_MODBUS_SLAVE is None or _LAST_MODBUS_SLAVE == next_addr:
        return 0.0
    return ms / 1000.0


def _pause_before_modbus_request(slave_addr: int) -> None:
    total = _inter_request_delay_s() + _slave_switch_extra_s(slave_addr)
    if total > 0:
        time.sleep(total)


def _record_modbus_slave(slave_addr: int) -> None:
    global _LAST_MODBUS_SLAVE
    _LAST_MODBUS_SLAVE = slave_addr


class ModbusRtu:
    """Half-duplex Modbus RTU master for RS485 sensors.

    Args:
        uart:     busio.UART configured at 9600 8N1 (timeout >= 0.5 s)
        de_pin:   digitalio.DigitalInOut driving DE / ~RE, or ``None``
                  for auto-direction adapters like the DFR0845 (no DE GPIO).
        timeout:  read timeout in seconds (default 0.5)
    """

    def __init__(
        self,
        uart,
        de_pin,
        timeout: float = 0.5,
        *,
        extra_turnaround_s: float = 0.0,
    ) -> None:
        self._uart = uart
        self._de = de_pin  # None for auto-direction TTL RS485 boards (no DE GPIO)
        self._timeout = timeout
        self._extra_turnaround_s = float(extra_turnaround_s)
        if self._de is not None:
            self._de.value = False  # start in receive mode

    def _char_time_s(self) -> float:
        """One 8N1 character time from the live UART baud (falls back to 9600)."""
        baud = getattr(self._uart, "baudrate", None) or _BAUD_RATE
        baud = max(int(baud), 300)
        return 10 / baud

    def _discard_rx(self) -> None:
        """Clear UART RX before a new request (leftovers from timeouts / noise)."""
        if hasattr(self._uart, "reset_input_buffer"):
            self._uart.reset_input_buffer()
            return
        deadline = time.monotonic() + 0.02
        while time.monotonic() < deadline:
            chunk = self._uart.read(64)
            if not chunk:
                break

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
        self._discard_rx()
        _pause_before_modbus_request(address)
        try:
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
        finally:
            _record_modbus_slave(address)

    def write_register(self, address: int, register: int, value: int) -> None:
        """Send FC 0x06 to write a single 16-bit register value.

        Args:
            address:  Modbus slave address
            register: register address
            value:    16-bit value to write
        """
        self._discard_rx()
        _pause_before_modbus_request(address)
        try:
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
        finally:
            _record_modbus_slave(address)

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
        if self._de is not None:
            self._de.value = True
        self._uart.write(frame)
        ct = self._char_time_s()
        time.sleep(len(frame) * ct + _TX_SETTLING_S)
        if self._de is not None:
            self._de.value = False
        interframe = 3.5 * ct + 0.002
        if self._de is None:
            interframe += _AUTO_DIR_EXTRA_S
            extra_ms = int(os.getenv("RS485_TURNAROUND_MS") or 0)
            if extra_ms > 0:
                interframe += extra_ms / 1000.0
        interframe += self._extra_turnaround_s
        time.sleep(interframe)

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

    def try_read_registers(
        self, address: int, register: int, count: int
    ) -> tuple[bool, list[int] | None, str]:
        """FC 0x03 read for diagnostics; never raises.

        Returns ``(ok, regs|None, message)``. On failure ``message`` includes a
        hex dump of any bytes captured on RX.
        """
        self._discard_rx()
        _pause_before_modbus_request(address)
        try:
            frame = self._build_frame(
                address,
                _FUNC_READ,
                register >> 8,
                register & 0xFF,
                count >> 8,
                count & 0xFF,
            )
            self._transmit(frame)
            expected_len = 3 + count * 2 + 2
            buf = bytearray()
            deadline = time.monotonic() + self._timeout
            while len(buf) < expected_len and time.monotonic() < deadline:
                chunk = self._uart.read(expected_len - len(buf))
                if chunk:
                    buf += chunk
            if len(buf) < expected_len:
                hx = buf.hex() if buf else "(empty)"
                return False, None, f"timeout {len(buf)}/{expected_len} bytes  rx={hx}"

            try:
                self._validate_crc(buf)
            except ValueError as exc:
                return False, None, f"{exc}  rx={buf.hex()}"

            if buf[1] & 0x80:
                exc_msg = (
                    f"modbus exception fc={buf[1]:#04x} code={buf[2]:#04x}  "
                    f"rx={buf.hex()}"
                )
                return (False, None, exc_msg)

            regs = [(buf[3 + i * 2] << 8) | buf[4 + i * 2] for i in range(count)]
            return True, regs, f"ok  rx={buf.hex()}"
        finally:
            _record_modbus_slave(address)

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
