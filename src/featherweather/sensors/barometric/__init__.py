"""Barometric pressure, altitude, and temperature sensor (BMP390).

Hardware: Adafruit BMP390 - Precision Barometric Pressure and Altimeter
          https://www.adafruit.com/product/4816
Chip: Bosch BMP390 / BMP390L
Interface: I2C (STEMMA QT), default address 0x77 (0x76 with SDO low)

Altitude is NOT derived here — GPS altitude is used to compute sea-level
normalized pressure (SLP) via BarometricReader.sea_level_pressure_from_altitude().
"""
