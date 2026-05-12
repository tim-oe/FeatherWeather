"""RS485 / Modbus RTU bus diagnostics (FeatherWeather).

Use when Modbus times out on the Feather but the same sensors work from a
host with a USB–RS485 adapter.  Typical checks: UART config, DE/auto mode,
post-TX turnaround sweep, and a short Modbus address scan.

Run from the REPL::

    from featherweather.hardware.rs485_diagnostic import Rs485Diagnostic
    Rs485Diagnostic().run_all()

Or deploy ``rs485_diag.py`` from the repo root as ``CIRCUITPY/code.py`` and
reset the board (see ``poetry run deploy --rs485-diag``).

Environment (``settings.toml``), all optional:

    RS485_DIAG_ADDRS       comma-separated unit IDs to scan (default below)
    RS485_DIAG_REG         holding register start, hex or decimal (default 0)
    RS485_DIAG_COUNT       number of 16-bit registers to read (default 1)
    RS485_DIAG_TIMEOUT_MS  per-attempt read timeout (default 1500)
    RS485_DIAG_SWEEP_ADDR  unit ID used for turnaround sweep (default 2)
    RS485_DIAG_LISTEN_MS   idle-bus listen window in ms (default 200)

Standard RS485 keys (``RS485_BAUD``, ``RS485_AUTO_DIRECTION``,
``RS485_TURNAROUND_MS``, etc.) still apply via ``get_rs485()`` / ``ModbusRtu``.
"""

from __future__ import annotations

import os
import time

from featherweather.hardware.rs485_bus import get_rs485
from featherweather.sensors.rs485.modbus_rtu import ModbusRtu

__all__ = ["Rs485Diagnostic"]

_DEFAULT_SCAN_ADDRS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return default
    return int(str(raw).strip(), 0)


def _parse_addr_list(raw: str | None) -> tuple[int, ...]:
    if raw is None or not str(raw).strip():
        return _DEFAULT_SCAN_ADDRS
    out: list[int] = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part, 0))
    return tuple(out) if out else _DEFAULT_SCAN_ADDRS


class Rs485Diagnostic:
    """Interactive RS485 line + Modbus RTU checks."""

    def __init__(self, log=print) -> None:
        self._log = log

    def log_bus_config(self) -> None:
        """Print UART / DE / env flags from the shared RS485 singleton."""
        uart, de_pin = get_rs485()
        baud = getattr(uart, "baudrate", "?")
        tout = getattr(uart, "timeout", "?")
        auto = os.getenv("RS485_AUTO_DIRECTION", "(unset)")
        tr_ms = os.getenv("RS485_TURNAROUND_MS", "(unset)")
        self._log("--- RS485 bus (singleton) ---")
        self._log(f"  UART baud={baud}  timeout={tout}s")
        self._log(f"  DE pin: {'None (auto TTL)' if de_pin is None else 'D12 active'}")
        swap = os.getenv("RS485_UART_SWAP", "(unset)")
        rxb = os.getenv("RS485_RX_BUFFER", "(unset)")
        self._log(f"  RS485_AUTO_DIRECTION={auto!r}  RS485_TURNAROUND_MS={tr_ms!r}")
        self._log(f"  RS485_UART_SWAP={swap!r}  RS485_RX_BUFFER={rxb!r}")

    def listen_idle_bus(self, window_ms: int | None = None) -> bytearray:
        """For ``window_ms``, read anything that arrives without sending a frame."""
        uart, _de = get_rs485()
        ms = (
            window_ms
            if window_ms is not None
            else _env_int("RS485_DIAG_LISTEN_MS", 200)
        )
        if hasattr(uart, "reset_input_buffer"):
            uart.reset_input_buffer()
        buf = bytearray()
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            chunk = uart.read(32)
            if chunk:
                buf += chunk
            else:
                time.sleep(0.01)
        self._log(f"--- idle listen {ms} ms ---")
        self._log(
            f"  captured {len(buf)} byte(s)  hex={buf.hex() if buf else '(none)'}"
        )
        return buf

    def sweep_turnaround(
        self,
        slave_id: int | None = None,
        register: int | None = None,
        count: int | None = None,
    ) -> None:
        """Try FC03 at one address with increasing ``extra_turnaround_s`` after TX."""
        sid = (
            slave_id
            if slave_id is not None
            else _env_int("RS485_DIAG_SWEEP_ADDR", 0x02)
        )
        reg = register if register is not None else _env_int("RS485_DIAG_REG", 0x0000)
        cnt = count if count is not None else _env_int("RS485_DIAG_COUNT", 1)
        timeout_s = _env_int("RS485_DIAG_TIMEOUT_MS", 1500) / 1000.0
        uart, de_pin = get_rs485()
        extras = (0.0, 0.005, 0.010, 0.020, 0.040, 0.080, 0.120)
        self._log("--- turnaround sweep (FC03 read) ---")
        self._log(f"  slave={sid}  reg={reg:#06x}  count={cnt}  timeout={timeout_s}s")
        m0 = ModbusRtu(uart, de_pin, timeout=timeout_s)
        tx_probe = m0._build_frame(
            sid,
            0x03,
            reg >> 8,
            reg & 0xFF,
            cnt >> 8,
            cnt & 0xFF,
        )
        self._log(f"  FC03 TX frame (expect on RS485): {tx_probe.hex()}")
        for extra in extras:
            m = ModbusRtu(
                uart,
                de_pin,
                timeout=timeout_s,
                extra_turnaround_s=extra,
            )
            ok, regs, msg = m.try_read_registers(sid, reg, cnt)
            reg_s = f"regs={regs}" if ok else ""
            self._log(f"  extra_tx={extra*1000:.0f} ms  ok={ok}  {reg_s}  {msg}")

    def scan_addresses(
        self,
        addresses: tuple[int, ...] | None = None,
        register: int | None = None,
        count: int | None = None,
    ) -> list[tuple[int, bool, str]]:
        """FC03 read at each address; returns list of (addr, ok, message)."""
        addrs = (
            addresses
            if addresses is not None
            else _parse_addr_list(os.getenv("RS485_DIAG_ADDRS"))
        )
        reg = register if register is not None else _env_int("RS485_DIAG_REG", 0x0000)
        cnt = count if count is not None else _env_int("RS485_DIAG_COUNT", 1)
        timeout_s = _env_int("RS485_DIAG_TIMEOUT_MS", 1500) / 1000.0
        uart, de_pin = get_rs485()
        m = ModbusRtu(uart, de_pin, timeout=timeout_s)
        self._log("--- Modbus address scan ---")
        self._log(f"  addrs={list(addrs)}  reg={reg:#06x}  count={cnt}")
        results: list[tuple[int, bool, str]] = []
        for addr in addrs:
            ok, regs, msg = m.try_read_registers(addr, reg, cnt)
            results.append((addr, ok, msg))
            mark = "OK " if ok else "-  "
            self._log(f"  {mark}addr {addr:3d}  {msg}")
        return results

    def run_all(self) -> list[tuple[int, bool, str]]:
        """Run listen → turnaround sweep → address scan; return scan results."""
        self._log("")
        self._log("FeatherWeather RS485 diagnostic")
        self._log("")
        self.log_bus_config()
        self.listen_idle_bus()
        self.sweep_turnaround()
        results = self.scan_addresses()
        self._log("")
        self._log("Done. If every attempt shows timeout rx=(empty):")
        self._log("  - A/B: same wires that work on the Pi? Swap A/B once.")
        self._log("  - GND: Feather + TTL adapter + sensor supply return.")
        self._log("  - Try RS485_UART_SWAP=true (then full power-cycle).")
        self._log("  - Sniff RS485 with Pi; TX hex above should match bus.")
        self._log("")
        return results
