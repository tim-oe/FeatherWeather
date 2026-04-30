"""Tipping bucket rainfall sensor reader for CircuitPython (SEN0575).

No official CircuitPython library — ports the DFRobot I2C reference implementation.

Reference: https://wiki.dfrobot.com/sen0575/
Source: https://github.com/DFRobot/DFRobot_RainfallSensor

IMPORTANT: Set the DIP switch on the sensor board to the I2C position before use.

Usage:
    import busio, board
    i2c = busio.I2C(board.SCL, board.SDA)
    reader = RainfallReader(i2c)
    data = reader.read()
    data.rainfall_window_mm = reader.read_window(hours=1)
"""

import time

from adafruit_bus_device.i2c_device import I2CDevice

from featherweather.sensors.rainfall.rainfall_data import RainfallData

__all__ = ["RainfallReader"]

_I2C_ADDR: int = 0x1D

_REG_PID: int = 0x00
_REG_CUMULATIVE_RAINFALL: int = 0x10
_REG_RAW_DATA: int = 0x14
_REG_SYS_TIME: int = 0x18
_REG_RAIN_HOUR: int = 0x26
_REG_TIME_RAINFALL: int = 0x0C
_REG_BASE_RAINFALL: int = 0x28

_RAINFALL_SCALE: float = 10000.0
_TIME_MINUTES_PER_HOUR: float = 60.0

_EXPECTED_VID: int = 0x3343
_EXPECTED_PID: int = 0x100C0

_WRITE_DELAY_S: float = 0.05


class RainfallReader:
    """CircuitPython reader for the SEN0575 tipping bucket rainfall sensor.

    I2C register protocol (all values little-endian):
        0x10  4 bytes  cumulative rainfall  raw / 10000 = mm
        0x14  4 bytes  tip count            raw integer
        0x18  2 bytes  system uptime        minutes
        0x26  1 byte   rolling window       write 1-24 (hours)
        0x0C  4 bytes  window rainfall      raw / 10000 = mm (after window write)
        0x28  2 bytes  base offset          write raw = mm * 10000
    """

    def __init__(self, i2c, address: int = _I2C_ADDR) -> None:
        """
        Args:
            i2c:     busio.I2C instance
            address: I2C address (default 0x1D)

        Raises:
            ValueError: if device VID/PID does not match SEN0575
        """
        self._device = I2CDevice(i2c, address)
        if not self._verify():
            raise ValueError("SEN0575 not found or VID/PID mismatch")

    def read(self) -> RainfallData:
        """Read cumulative rainfall, tip count, and uptime.

        Returns:
            RainfallData populated with current sensor values.
            rainfall_window_mm is None; call read_window() separately if needed.
        """
        data = RainfallData()
        data.cumulative_rainfall_mm = self._read_rainfall(_REG_CUMULATIVE_RAINFALL)
        data.bucket_count = self._read_uint32(_REG_RAW_DATA)
        data.working_time_h = self._read_uint16(_REG_SYS_TIME) / _TIME_MINUTES_PER_HOUR
        return data

    def read_window(self, hours: int) -> float:
        """Get cumulative rainfall within a rolling time window.

        Args:
            hours: window length in hours (1 - 24)

        Returns:
            Rainfall in mm over the specified window

        Raises:
            ValueError: if hours is outside the 1-24 range
        """
        if not 1 <= hours <= 24:
            raise ValueError(f"hours must be 1-24, got {hours}")
        self._write_register(_REG_RAIN_HOUR, [hours])
        return self._read_rainfall(_REG_TIME_RAINFALL)

    def reset_accumulation(self, base_mm: float = 0.0) -> None:
        """Set the cumulative rainfall base value (effectively resets the counter)."""
        raw = int(base_mm * _RAINFALL_SCALE)
        self._write_register(_REG_BASE_RAINFALL, [raw & 0xFF, raw >> 8])

    def _verify(self) -> bool:
        buf = self._read_register(_REG_PID, 4)
        pid = buf[0] | (buf[1] << 8) | ((buf[3] & 0xC0) << 10)
        vid = buf[2] | ((buf[3] & 0x3F) << 8)
        return vid == _EXPECTED_VID and pid == _EXPECTED_PID

    def _read_rainfall(self, reg: int) -> float:
        return self._read_uint32(reg) / _RAINFALL_SCALE

    def _read_uint32(self, reg: int) -> int:
        buf = self._read_register(reg, 4)
        return buf[0] | (buf[1] << 8) | (buf[2] << 16) | (buf[3] << 24)

    def _read_uint16(self, reg: int) -> int:
        buf = self._read_register(reg, 2)
        return buf[0] | (buf[1] << 8)

    def _read_register(self, reg: int, length: int) -> bytearray:
        buf = bytearray(length)
        with self._device as dev:
            dev.write_then_readinto(bytes([reg]), buf)
        return buf

    def _write_register(self, reg: int, data: list) -> None:
        with self._device as dev:
            dev.write(bytes([reg]) + bytes(data))
        time.sleep(_WRITE_DELAY_S)
