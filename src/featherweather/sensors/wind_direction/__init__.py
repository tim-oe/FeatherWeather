"""Wind direction / vane sensor (SEN0482).

Hardware: DFRobot RS485 Wind Direction Transmitter V2 (SEN0482)
          https://wiki.dfrobot.com/sen0482/
Protocol: Modbus RTU, RS485, 9600 8N1
Default Modbus address: 0x02

IMPORTANT: SEN0483 wind speed sensor also defaults to address 0x02.
Change one before putting both on the same RS485 bus.

White dot on sensor housing must face true north during installation.
"""
