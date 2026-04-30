"""Tipping bucket rainfall sensor (SEN0575).

Hardware: DFRobot Gravity Tipping Bucket Rainfall Sensor (SEN0575)
          https://wiki.dfrobot.com/sen0575/
Interface: I2C, fixed address 0x1D (DIP switch must be set to I2C position)
Resolution: 0.28 mm per tip

No official CircuitPython library — uses a ported raw I2C driver.

Ported from https://github.com/DFRobot/DFRobot_RainfallSensor
(python/DFRobot_RainfallSensor.py, DFRobot_RainfallSensor_I2C class)
"""
