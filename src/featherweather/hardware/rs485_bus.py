"""Shared RS485 UART + DE-pin singleton for FeatherWeather.

The MAX3485 transceiver is half-duplex; all RS485 Modbus sensors share
one busio.UART and one direction-control GPIO.

Pin assignments (fixed to match the FeatherWeather PCB layout):
    UART TX   board.A0  (GPIO26)
    UART RX   board.A1  (GPIO25)
    DE / ~RE  board.D12 (GPIO12 — LOW at boot via strapping, safe default)

Environment variables:
    RS485_BAUD        baud rate in bps     (default 9600)
    RS485_TIMEOUT_MS  read timeout in ms   (default 500)
"""

import os

import board
import busio
import digitalio

__all__ = ["get_rs485"]

_uart = None
_de_pin = None


def get_rs485():
    """Return (uart, de_pin), creating them on the first call.

    Raises on any hardware failure (pin conflict, UART unavailable, etc.)
    so that RS485 sensor readers can be skipped cleanly via _try_init().
    """
    global _uart, _de_pin  # noqa: PLW0603
    if _uart is None:
        baud = int(os.getenv("RS485_BAUD") or 9600)
        timeout_s = int(os.getenv("RS485_TIMEOUT_MS") or 500) / 1000
        _uart = busio.UART(board.A0, board.A1, baudrate=baud, timeout=timeout_s)
        _de_pin = digitalio.DigitalInOut(board.D12)
        _de_pin.direction = digitalio.Direction.OUTPUT
    return _uart, _de_pin
