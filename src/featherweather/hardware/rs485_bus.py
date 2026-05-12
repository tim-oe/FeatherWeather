"""Shared RS485 UART + DE-pin singleton for FeatherWeather.

The MAX3485 transceiver is half-duplex; all RS485 Modbus sensors share
one busio.UART and one direction-control GPIO.

Pin assignments (default; see RS485_UART_SWAP):
    UART TX   board.A0  (GPIO26)  — or A1 when RS485_UART_SWAP is true
    UART RX   board.A1  (GPIO25)  — or A0 when RS485_UART_SWAP is true
    DE / ~RE  board.D12 (GPIO12 — LOW at boot via strapping, safe default)

Environment variables:
    RS485_BAUD             baud rate in bps     (default 9600)
    RS485_TIMEOUT_MS       read timeout in ms   (default 500)
    RS485_TURNAROUND_MS    extra post-TX delay for auto-direction TTL boards
                           (optional; default extra is 15 ms when DE is off)
    RS485_AUTO_DIRECTION   when true/1/yes/on, skip GPIO12 DE/~RE — use for
                           common 4-pin TTL adapters (V G TX RX) that switch
                           direction internally; UART stays on A0/A1. On ESP32,
                           GPIO12 is a flash strapping pin: avoid driving it at
                           reset; 4-pin users should set this true and never
                           wire DE to D12 unless the line is guaranteed low at boot.
    RS485_UART_SWAP        when true, use UART TX=A1 RX=A0 (swap vs default A0/A1)
    RS485_RX_BUFFER        UART RX ring buffer size (default 256; 0 = omit kwarg)
    RS485_INTER_REQUEST_MS optional ms pause before each Modbus frame (default 0;
                           try 35–50 if slaves miss only in rapid back-to-back reads)
    RS485_SLAVE_SWITCH_MS  extra ms when changing Modbus slave address (default 80;
                           set 0 to disable; shared across all ModbusRtu instances)
"""

import os

import board
import busio
import digitalio

__all__ = ["get_rs485"]

_uart = None
_de_pin = None


def _env_truthy(key: str) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _open_rs485_uart(baud: int, timeout_s: float) -> busio.UART:
    """Create UART for RS485; swap pins and buffer size are optional via env."""
    swap = _env_truthy("RS485_UART_SWAP")
    tx_pin = board.A1 if swap else board.A0
    rx_pin = board.A0 if swap else board.A1
    buf = 256
    raw_buf = os.getenv("RS485_RX_BUFFER")
    if raw_buf is not None and str(raw_buf).strip():
        buf = int(str(raw_buf).strip(), 0)
    kwargs = {"baudrate": baud, "timeout": timeout_s}
    if buf > 0:
        kwargs["receiver_buffer_size"] = min(max(buf, 32), 1024)
    try:
        return busio.UART(tx_pin, rx_pin, **kwargs)
    except TypeError:
        kwargs.pop("receiver_buffer_size", None)
        return busio.UART(tx_pin, rx_pin, **kwargs)


def get_rs485():
    """Return (uart, de_pin), creating them on the first call.

    ``de_pin`` is ``None`` when ``RS485_AUTO_DIRECTION`` is set in
    ``settings.toml`` (TTL modules with only V/G/TX/RX and no DE pin).

    Raises on any hardware failure (pin conflict, UART unavailable, etc.)
    so that RS485 sensor readers can be skipped cleanly via _try_init().
    """
    global _uart, _de_pin  # noqa: PLW0603
    if _uart is None:
        baud = int(os.getenv("RS485_BAUD") or 9600)
        timeout_s = int(os.getenv("RS485_TIMEOUT_MS") or 500) / 1000
        _uart = _open_rs485_uart(baud, timeout_s)
        if _env_truthy("RS485_AUTO_DIRECTION"):
            _de_pin = None
        else:
            _de_pin = digitalio.DigitalInOut(board.D12)
            _de_pin.direction = digitalio.Direction.OUTPUT
    return _uart, _de_pin
