"""Read and display live data from DFRobot RS485 weather sensors.

Run this on a Raspberry Pi (or any Linux host) with a USB serial adapter.
Multiple sensors can be on the bus at once (each must have a unique address).

The adapter can be:

- USB–RS485 (A/B wired straight to the sensor pair), or
- USB–TTL (e.g. Waveshare TTL mode) wired to the **TTL** side of a MAX3485
  board whose **A/B** side goes to the sensors (Feather not in circuit).

Requirements:
    pip install pyserial

Usage:
    # one-shot read
    python scripts/read_rs485_sensor.py --port /dev/ttyUSB0 --sensor SEN0483 --addr 0x03

    # continuous reads every 2 seconds
    python scripts/read_rs485_sensor.py --port /dev/ttyUSB0 --sensor SEN0483 --addr 0x03 --loop

    # all three sensors on the shared bus
    python scripts/read_rs485_sensor.py --port /dev/ttyUSB0 --sensor SEN0482 --addr 0x02
    python scripts/read_rs485_sensor.py --port /dev/ttyUSB0 --sensor SEN0483 --addr 0x03
    python scripts/read_rs485_sensor.py --port /dev/ttyUSB0 --sensor SEN0644 --addr 0x01

Sensor default addresses:
    SEN0482  Wind Direction  0x02
    SEN0483  Wind Speed      0x03  (reprogrammed from default 0x02)
    SEN0644  Illuminance     0x01
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    print("pyserial not found. Install it with:  pip install pyserial")
    sys.exit(1)

_BAUD: int = 9600
_TIMEOUT_S: float = 1.0

_FC_READ: int = 0x03

_DIRECTION_LABELS: tuple = (
    "N", "NNE", "NE", "ENE",
    "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW",
    "W", "WNW", "NW", "NNW",
)

_BEAUFORT: tuple = (
    (0.3, "Calm"),
    (1.6, "Light air"),
    (3.4, "Light breeze"),
    (5.5, "Gentle breeze"),
    (8.0, "Moderate breeze"),
    (10.8, "Fresh breeze"),
    (13.9, "Strong breeze"),
    (17.2, "Near gale"),
    (20.8, "Gale"),
    (24.5, "Strong gale"),
    (28.5, "Storm"),
    (32.4, "Violent storm"),
    (float("inf"), "Hurricane"),
)

_SENSOR_DEFAULTS: dict[str, int] = {
    "SEN0482": 0x02,
    "SEN0483": 0x03,
    "SEN0644": 0x01,
}


# ---------------------------------------------------------------------------
# Minimal Modbus RTU helpers
# ---------------------------------------------------------------------------


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _build_frame(*fields: int) -> bytes:
    payload = bytes(fields)
    crc = _crc16(payload)
    return payload + bytes([crc & 0xFF, crc >> 8])


def _validate_response(raw: bytes, expected_len: int) -> None:
    if len(raw) < expected_len:
        raise ValueError(
            f"Short response: expected {expected_len} bytes, got {len(raw)}"
            + (f"  raw={raw.hex()}" if raw else "")
        )
    crc_computed = _crc16(raw[:-2])
    crc_received = raw[-2] | (raw[-1] << 8)
    if crc_computed != crc_received:
        raise ValueError(
            f"CRC mismatch: computed {crc_computed:#06x},"
            f" received {crc_received:#06x}  raw={raw.hex()}"
        )
    if raw[1] & 0x80:
        raise ValueError(f"Modbus exception from sensor: code {raw[2]:#04x}")


def read_registers(
    port: serial.Serial,
    addr: int,
    register: int,
    count: int,
    *,
    post_tx_ms: float = 200.0,
    read_wait_s: float = 2.0,
    verbose: bool = False,
) -> list[int]:
    """FC 0x03 — read `count` holding registers starting at `register`."""
    frame = _build_frame(
        addr,
        _FC_READ,
        register >> 8,
        register & 0xFF,
        count >> 8,
        count & 0xFF,
    )
    port.reset_input_buffer()
    if verbose:
        print(f"  [verbose] TX {len(frame)} bytes: {frame.hex()}")
    port.write(frame)
    port.flush()
    time.sleep(post_tx_ms / 1000.0)
    expected = 3 + count * 2 + 2  # addr + FC + byte_count + data + CRC
    buf = bytearray()
    deadline = time.time() + read_wait_s
    old_timeout = port.timeout
    try:
        port.timeout = 0.05
        while len(buf) < expected and time.time() < deadline:
            chunk = port.read(expected - len(buf))
            if chunk:
                buf += chunk
    finally:
        port.timeout = old_timeout
    raw = bytes(buf)
    if verbose:
        print(f"  [verbose] RX {len(raw)} bytes: {raw.hex() if raw else '(none)'}")
    _validate_response(raw, expected)
    return [(raw[3 + i * 2] << 8) | raw[4 + i * 2] for i in range(count)]


# ---------------------------------------------------------------------------
# Per-sensor decode
# ---------------------------------------------------------------------------


def _beaufort(speed_ms: float) -> str:
    for threshold, label in _BEAUFORT:
        if speed_ms < threshold:
            return label
    return "Hurricane"


def read_sen0482(port: serial.Serial, addr: int, **kw) -> None:
    """SEN0482 Wind Direction."""
    regs = read_registers(port, addr, 0x0000, count=2, **kw)
    degrees = regs[0] / 10.0
    code = min(regs[1], len(_DIRECTION_LABELS) - 1)
    label = _DIRECTION_LABELS[code]
    print(f"  Wind Direction : {degrees:5.1f} °   {label}")


def read_sen0483(port: serial.Serial, addr: int, **kw) -> None:
    """SEN0483 Wind Speed."""
    regs = read_registers(port, addr, 0x0000, count=1, **kw)
    speed_ms = regs[0] / 10.0
    speed_kmh = speed_ms * 3.6
    bf = _beaufort(speed_ms)
    print(f"  Wind Speed     : {speed_ms:5.1f} m/s  ({speed_kmh:.1f} km/h)  {bf}")


def read_sen0644(port: serial.Serial, addr: int, **kw) -> None:
    """SEN0644 Illuminance."""
    regs = read_registers(port, addr, 0x0002, count=2, **kw)
    lux = ((regs[0] << 16) | regs[1]) / 1000.0
    print(f"  Illuminance    : {lux:,.1f} lux")


_READERS = {
    "SEN0482": read_sen0482,
    "SEN0483": read_sen0483,
    "SEN0644": read_sen0644,
}

_DESCRIPTIONS = {
    "SEN0482": "Wind Direction",
    "SEN0483": "Wind Speed",
    "SEN0644": "Illuminance",
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read live data from a DFRobot RS485 weather sensor."
    )
    parser.add_argument(
        "--port",
        required=True,
        help="Serial port of USB-RS485 adapter (e.g. /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--sensor",
        required=True,
        choices=list(_READERS.keys()),
        help="Target sensor model",
    )
    parser.add_argument(
        "--addr",
        type=lambda x: int(x, 0),
        default=None,
        help="Modbus address of the sensor (decimal or 0x hex). Defaults per sensor: "
             "SEN0482=0x02, SEN0483=0x03, SEN0644=0x01",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Read continuously until Ctrl+C",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds between reads in --loop mode (default 2.0)",
    )
    parser.add_argument(
        "--post-tx-ms",
        type=float,
        default=200.0,
        help="Delay after TX before reading RX (default 200). Try 400 for some "
        "auto-direction MAX3485 TTL boards.",
    )
    parser.add_argument(
        "--read-wait-s",
        type=float,
        default=2.0,
        help="Max seconds to accumulate a full Modbus response (default 2.0).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print Modbus TX/RX byte hex for debugging.",
    )
    args = parser.parse_args()

    addr = args.addr if args.addr is not None else _SENSOR_DEFAULTS[args.sensor]
    reader = _READERS[args.sensor]
    description = _DESCRIPTIONS[args.sensor]
    read_kw = {
        "post_tx_ms": args.post_tx_ms,
        "read_wait_s": args.read_wait_s,
        "verbose": args.verbose,
    }

    print(f"Sensor  : {args.sensor}  ({description})")
    print(f"Port    : {args.port} @ {_BAUD} baud")
    print(f"Address : {addr:#04x} ({addr})")
    print()

    with serial.Serial(args.port, baudrate=_BAUD, timeout=_TIMEOUT_S) as port:
        if args.loop:
            print("Reading continuously — press Ctrl+C to stop.\n")
            try:
                while True:
                    ts = time.strftime("%H:%M:%S")
                    try:
                        reader(port, addr, **read_kw)
                    except ValueError as exc:
                        print(f"  [{ts}] ERROR: {exc}")
                    else:
                        # reprint timestamp on the same line group
                        print(f"  [{ts}]")
                    print()
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\nStopped.")
        else:
            try:
                reader(port, addr, **read_kw)
            except ValueError as exc:
                print(f"  ERROR: {exc}")
                print(
                    "  Check: Waveshare in TTL mode (not RS485), 3.3 V level if needed, "
                    "GND common, sensor power, address. Swap TXD/RXD once at the MAX3485 "
                    "header. Try:  --verbose  --post-tx-ms 400"
                )
                sys.exit(1)


if __name__ == "__main__":
    main()
