"""Shared RS485 / Modbus RTU support for DFRobot weather sensors.

All three RS485 sensors (SEN0482 wind vane, SEN0483 wind speed, SEN0644 light)
speak Modbus RTU at 9600 8N1 and share the same physical RS485 bus wired
through a MAX3485 TTL-to-RS485 module.

Bus wiring (MAX3485 → ESP32 Feather V2):
    MAX3485 DI  → ESP32 TX pin
    MAX3485 RO  → ESP32 RX pin
    MAX3485 DE  → ESP32 GPIO (de_pin, shared, active-HIGH = transmit)
    MAX3485 /RE → tie to DE pin

Default Modbus addresses:
    SEN0482 wind vane:   0x02
    SEN0483 wind speed:  0x02  ← ADDRESS CONFLICT if both on same bus
    SEN0644 light:       0x01

IMPORTANT: SEN0482 and SEN0483 both default to address 0x02. Before wiring
them to the same bus you must change one of them. Use the reader's
set_address() method while each sensor is connected alone to reassign.
"""
