"""Shared RS485 / Modbus RTU support for DFRobot weather sensors.

All three RS485 sensors (SEN0482 wind vane, SEN0483 wind speed, SEN0644 light)
speak Modbus RTU at 9600 8N1 and share the same physical RS485 bus wired
through a DFRobot Active Isolated RS485-to-UART adapter (DFR0845).

Bus wiring (DFR0845 Gravity UART connector → ESP32 Feather V2):
    DFR0845 + (VCC) → ESP32 USB (5V VBUS — NOT 3V; internal boost converter
                       overloads the AP2112K LDO if powered from the 3V rail)
    DFR0845 - (GND) → ESP32 GND
    DFR0845 T (TX)  → ESP32 A1  (UART RX, GPIO25)
    DFR0845 R (RX)  → ESP32 A0  (UART TX, GPIO26)

RS485 screw terminal:
    A / B           → RS485 bus A / B shared by all three sensors
    12V / GND       → sensor power (isolated RS485-side ground)

External 12V supply:
    Connect to the DFR0845 12V-IN screw terminal when sensors draw > 160 mA
    combined. The module's internal boost converter generates 12V from UART
    VCC and backfeeds the 12V-IN terminals even when the external supply is
    off — always power the external supply on before or simultaneously with
    USB so it dominates and reliably powers the sensors.

Direction control:
    The DFR0845 handles TX/RX switching internally. Set RS485_AUTO_DIRECTION
    = true in settings.toml; D12 (GPIO12) must NOT be wired to the adapter.

Default Modbus addresses:
    SEN0482 wind vane:   0x02
    SEN0483 wind speed:  0x02  ← ADDRESS CONFLICT if both on same bus
    SEN0644 light:       0x01

IMPORTANT: SEN0482 and SEN0483 both default to address 0x02. Before wiring
them to the same bus you must change one of them. Use the reader's
set_address() method while each sensor is connected alone to reassign.
"""
