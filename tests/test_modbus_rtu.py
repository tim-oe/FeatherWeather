"""Tests for ModbusRtu.try_read_registers and extra turnaround."""

import pytest

from featherweather.sensors.rs485.modbus_rtu import ModbusRtu


class _FakeUART:
    """RX buffer is filled when ``write()`` runs (simulates slave reply after TX)."""

    def __init__(self, response_after_tx: bytes | None) -> None:
        self.baudrate = 9600
        self.timeout = 1.0
        self._response_after_tx = response_after_tx
        self._rx = bytearray()
        self.written: bytes = b""

    def reset_input_buffer(self) -> None:
        self._rx.clear()

    def write(self, data: bytes) -> None:
        self.written = bytes(data)
        if self._response_after_tx:
            self._rx = bytearray(self._response_after_tx)

    def read(self, nbytes: int):
        if not self._rx:
            return None
        take = min(nbytes, len(self._rx))
        out = bytes(self._rx[:take])
        del self._rx[:take]
        return out


def _crc_body(body: bytes) -> bytes:
    crc = ModbusRtu._crc16(bytearray(body))
    return body + bytes([crc & 0xFF, crc >> 8])


def test_try_read_registers_success() -> None:
    """FC03 response for slave 2, one register value 0x0123."""
    body = bytes([0x02, 0x03, 0x02, 0x01, 0x23])
    resp = _crc_body(body)
    uart = _FakeUART(resp)
    m = ModbusRtu(uart, None, timeout=0.5)
    ok, regs, msg = m.try_read_registers(2, 0x0000, 1)
    assert ok is True
    assert regs == [0x0123]
    assert "ok" in msg


def test_try_read_registers_timeout_empty() -> None:
    uart = _FakeUART(None)
    m = ModbusRtu(uart, None, timeout=0.05)
    ok, regs, msg = m.try_read_registers(2, 0x0000, 1)
    assert ok is False
    assert regs is None
    assert "timeout" in msg
    assert "empty" in msg


def test_try_read_registers_crc_error() -> None:
    body = bytes([0x02, 0x03, 0x02, 0x01, 0x23, 0x00, 0x00])  # wrong CRC
    uart = _FakeUART(body)
    m = ModbusRtu(uart, None, timeout=0.5)
    ok, regs, msg = m.try_read_registers(2, 0x0000, 1)
    assert ok is False
    assert regs is None
    assert "CRC" in msg


def test_extra_turnaround_sleeps_longer(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    def _fake_sleep(dt: float) -> None:
        sleeps.append(dt)

    monkeypatch.setattr(
        "featherweather.sensors.rs485.modbus_rtu.time.sleep", _fake_sleep
    )
    uart = _FakeUART(None)
    m = ModbusRtu(uart, None, timeout=0.05, extra_turnaround_s=0.05)
    frame = m._build_frame(2, 0x03, 0x00, 0x00, 0x00, 0x01)
    m._transmit(frame)
    assert sleeps, "expected transmit to sleep"
    assert sleeps[-1] >= 0.05
