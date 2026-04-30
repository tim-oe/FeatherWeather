"""SD card / storage module interface for Adalogger FeatherWing [2922].

Hardware: microSD socket on Adalogger FeatherWing
Interface: SPI
Notes:
    - CS pin is the dedicated SD_CS pin on the FeatherWing
    - Shares SPI bus with other SPI peripherals
    - Supports FAT16/FAT32 formatted cards
"""
