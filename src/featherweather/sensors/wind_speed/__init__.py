"""Wind speed / anemometer sensor (SEN0483).

Hardware: DFRobot RS485 Wind Speed Transmitter (SEN0483)
          https://wiki.dfrobot.com/sen0483/
Protocol: Modbus RTU, RS485, 9600 8N1
Default Modbus address: 0x02

IMPORTANT: SEN0482 wind direction sensor also defaults to address 0x02.
Change one before putting both on the same RS485 bus.
"""
