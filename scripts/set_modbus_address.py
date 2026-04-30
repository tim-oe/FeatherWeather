"""One-time Modbus RTU address reprogramming utility.

Run this on a Raspberry Pi (or any Linux host) with a USB-to-RS485 adapter.
Connect ONLY the target sensor to the bus before running — do not have other
RS485 devices connected.

Requirements:
    pip install pyserial

Usage:
    python scripts/set_modbus_address.py --port /dev/ttyUSB0 \\
        --current-addr 2 --new-addr 3 --sensor SEN0483

    # verify the new address (read-only, does not change anything):
    python scripts/set_modbus_address.py --port /dev/ttyUSB0 \\
        --current-addr 3 --new-addr 3 --sensor SEN0483 --verify-only

Wiring (USB-to-RS485 adapter → sensor):
    Adapter A  →  Sensor A+
    Adapter B  →  Sensor B-
    GND        →  Sensor GND
    5 V or 12 V power supply  →  Sensor VCC + GND (check sensor datasheet)

The USB adapter handles DE/RE direction switching internally — no extra GPIO
needed. The sensor must be powered before running the script.

After the address is written, power-cycle the sensor to activate the new
address. Then update _WIND_SPD_ADDR in code.py to match.

Sensor register map (address register):
    SEN0482  Wind Direction   reg 0x1000  default addr 0x02
    SEN0483  Wind Speed       reg 0x1000  default addr 0x02
    SEN0644  Illuminance      reg 0x0064  default addr 0x01
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    print("pyserial not found. Install it with:  pip install pyserial")
    sys.exit(1)

# Register that holds the Modbus slave address for each sensor
_ADDRESS_REGISTER: dict[str, int] = {
    "SEN0482": 0x1000,
    "SEN0483": 0x1000,
    "SEN0644": 0x0064,
}

_BAUD: int = 9600
_TIMEOUT_S: float = 1.0

# Modbus FC
_FC_READ: int = 0x03
_FC_WRITE_SINGLE: int = 0x06  # not used for address change — kept for reference
_FC_WRITE_MULTI: int = 0x10


# ---------------------------------------------------------------------------
# Minimal Modbus RTU helpers (pure pyserial, no extra library)
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
            f"  raw={raw.hex()}"
        )
    crc_computed = _crc16(raw[:-2])
    crc_received = raw[-2] | (raw[-1] << 8)
    if crc_computed != crc_received:
        raise ValueError(
            f"CRC mismatch: computed {crc_computed:#06x},"
            f" received {crc_received:#06x}"
            f"  raw={raw.hex()}"
        )
    if raw[1] & 0x80:
        raise ValueError(f"Modbus exception from sensor: code {raw[2]:#04x}")


def read_register(port: serial.Serial, addr: int, register: int) -> int:
    """Read one 16-bit holding register (FC 0x03). Returns the register value."""
    frame = _build_frame(
        addr, _FC_READ,
        register >> 8, register & 0xFF,
        0x00, 0x01,  # count = 1
    )
    port.reset_input_buffer()
    port.write(frame)
    time.sleep(0.2)  # increased from 0.05 — USB-RS485 adapters need ~100-200 ms to flip TX→RX
    raw = port.read(7)  # addr + FC + byte_count + 2 data bytes + 2 CRC
    _validate_response(raw, 7)
    return (raw[3] << 8) | raw[4]


def write_address_register(
    port: serial.Serial, register: int, value: int
) -> None:
    """Write the Modbus slave address register using FC 0x10 sent to broadcast (0x00).

    DFRobot RS485 sensors (SEN0482/SEN0483/SEN0644) require FC 0x10 (write multiple
    registers) sent to address 0x00 (broadcast) to change the slave address.
    FC 0x06 to the sensor's current address is silently ignored.
    """
    frame = _build_frame(
        0x00,               # broadcast address
        _FC_WRITE_MULTI,
        register >> 8, register & 0xFF,
        0x00, 0x01,         # quantity of registers = 1
        0x02,               # byte count = 2
        value >> 8, value & 0xFF,
    )
    port.reset_input_buffer()
    port.write(frame)
    time.sleep(0.2)
    # Sensor responds with 8 bytes; broadcast may yield no response on some units —
    # accept either outcome since the write is fire-and-verify.
    raw = port.read(8)
    if len(raw) == 0:
        return  # no echo — normal for broadcast; verify by reading back
    _validate_response(raw, 8)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reprogram Modbus slave address of a DFRobot RS485 sensor."
    )
    parser.add_argument(
        "--port",
        required=True,
        help="Serial port of USB-RS485 adapter (e.g. /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--sensor",
        required=True,
        choices=list(_ADDRESS_REGISTER.keys()),
        help="Target sensor model",
    )
    parser.add_argument(
        "--current-addr",
        type=lambda x: int(x, 0),
        required=True,
        help="Current Modbus address of the sensor (decimal or 0x hex)",
    )
    parser.add_argument(
        "--new-addr",
        type=lambda x: int(x, 0),
        required=True,
        help="New Modbus address to assign (1-247)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only read back the current address register; do not write",
    )
    args = parser.parse_args()

    if not 1 <= args.new_addr <= 247:
        print(f"Error: new address {args.new_addr} out of range (1-247)")
        sys.exit(1)

    addr_reg = _ADDRESS_REGISTER[args.sensor]

    print(f"Sensor  : {args.sensor}")
    print(f"Port    : {args.port} @ {_BAUD} baud")
    print(f"Addr reg: {addr_reg:#06x}")
    print()

    with serial.Serial(args.port, baudrate=_BAUD, timeout=_TIMEOUT_S) as port:
        # Verify we can talk to the sensor at the claimed current address
        print(f"[1/3] Reading current address register from slave {args.current_addr:#04x} …")
        try:
            current_val = read_register(port, args.current_addr, addr_reg)
        except ValueError as exc:
            print(f"  FAILED: {exc}")
            print(
                "  Check wiring, power, and that the sensor is the only device on the bus."
            )
            sys.exit(1)
        print(f"  Address register value = {current_val:#04x} ({current_val})")

        if args.verify_only:
            print("\nVerify-only mode — nothing written.")
            sys.exit(0)

        if args.current_addr == args.new_addr:
            print(f"\nCurrent address already is {args.new_addr}. Nothing to do.")
            sys.exit(0)

        # Write the new address (FC 0x10 to broadcast address 0x00)
        print(f"\n[2/3] Writing new address {args.new_addr:#04x} ({args.new_addr}) …")
        try:
            write_address_register(port, addr_reg, args.new_addr)
        except ValueError as exc:
            print(f"  FAILED: {exc}")
            sys.exit(1)
        print("  Write command accepted.")

        # Confirm by reading back at the new address after power-cycle note
        print(
            f"\n[3/3] Verifying … (reading address register at new addr {args.new_addr:#04x})"
        )
        print("  NOTE: some sensors require a power-cycle before the new address activates.")
        print("  If verification fails, power-cycle and re-run with --verify-only.")
        time.sleep(0.2)
        try:
            readback = read_register(port, args.new_addr, addr_reg)
            print(f"  Readback = {readback:#04x} ({readback})")
            if readback == args.new_addr:
                print("\n  SUCCESS — address updated and active without power-cycle.")
            else:
                print(
                    f"\n  Unexpected value {readback}. Power-cycle the sensor and"
                    " re-run with --verify-only."
                )
        except ValueError:
            print(
                "  Could not read at new address yet — this is normal."
                " Power-cycle the sensor and re-run with --verify-only."
            )

    print("\nDone. Update _WIND_SPD_ADDR in code.py to match the new address.")


if __name__ == "__main__":
    main()
