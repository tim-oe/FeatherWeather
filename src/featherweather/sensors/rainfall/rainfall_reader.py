"""Tipping bucket rainfall sensor reader for CircuitPython (SEN0575).

No official CircuitPython library — ports the DFRobot I2C reference implementation.

Reference: https://wiki.dfrobot.com/sen0575/
Source: https://github.com/DFRobot/DFRobot_RainfallSensor

IMPORTANT: Set the DIP switch on the sensor board to the I2C position before use.

WIRING (STEMMA QT ↔ DFRobot Gravity JST):
    The DFRobot pigtail uses **V=black, G=red** (inverted from STEMMA QT).
    When bridging a STEMMA QT cable to the SEN0575's JST, swap red and black:

        STEMMA QT            DFRobot JST (silkscreen)
        ---------            ------------------------
        red    (3V3)   <-->  black  (V)
        black  (GND)   <-->  red    (G)
        blue   (SDA)   <-->  green  (D)
        yellow (SCL)   <-->  white  (C)

    Verify with a multimeter before powering on — reversing 3V3/GND will
    damage the sensor's on-board MCU.

Environment variable:
    RAIN_ADDR  I2C address override, hex or decimal (default 0x1D)

Usage:
    reader = RainfallReader()
    reader.read(payload)           # populates payload.rainfall (cumulative + 1hr)
    # or for diagnostics:
    data = RainfallReader.verify()
"""

import time

from adafruit_bus_device.i2c_device import I2CDevice

from featherweather.sensors.i2c_sensor_base import I2cSensorBase
from featherweather.sensors.rainfall.rainfall_data import RainfallData
from featherweather.storage.weather_payload import WeatherPayload

__all__ = ["RainfallReader"]

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


class RainfallReader(I2cSensorBase):
    """CircuitPython reader for the SEN0575 tipping bucket rainfall sensor.

    I2C register protocol (all values little-endian):
        0x10  4 bytes  cumulative rainfall  raw / 10000 = mm
        0x14  4 bytes  tip count            raw integer
        0x18  2 bytes  system uptime        minutes
        0x26  1 byte   rolling window       write 1-24 (hours)
        0x0C  4 bytes  window rainfall      raw / 10000 = mm (after window write)
        0x28  2 bytes  base offset          write raw = mm * 10000
    """

    _I2C_ADDR: int = 0x1D
    _LABEL: str = "SEN0575 (Rainfall)"

    def __init__(self, address: int | None = None) -> None:
        import os  # noqa: PLC0415

        if address is None:
            raw = os.getenv("RAIN_ADDR")
            if raw:
                address = int(raw, 0)
        super().__init__(address)

    def _init_device(self, i2c, address: int) -> None:
        self._device = I2CDevice(i2c, address)
        if not self._verify_device():
            raise ValueError("SEN0575 not found or VID/PID mismatch")

    def read(self, payload: WeatherPayload) -> None:
        """Read cumulative rainfall, tip count, uptime, and the 1-hour rolling
        window, then set payload.rainfall.

        Args:
            payload: in-progress WeatherPayload; payload.rainfall is written.
        """
        data = RainfallData()
        data.cumulative_rainfall_mm = self._read_rainfall(_REG_CUMULATIVE_RAINFALL)
        data.bucket_count = self._read_uint32(_REG_RAW_DATA)
        data.working_time_h = self._read_uint16(_REG_SYS_TIME) / _TIME_MINUTES_PER_HOUR
        data.rainfall_window_mm = self.read_window(hours=1)
        print(data)
        payload.rainfall = data

    @classmethod
    def verify(cls) -> "RainfallData":
        """Instantiate, take one reading, and return the RainfallData.

        Raises on any hardware or communication failure (including VID/PID mismatch).
        """
        sensor = cls()
        payload = WeatherPayload()
        sensor.read(payload)
        return payload.rainfall

    def read_window(self, hours: int) -> float:
        """Get cumulative rainfall within a rolling time window.

        Args:
            hours: window length in hours (1 - 24)

        Returns:
            Rainfall in mm over the specified window.

        Raises:
            ValueError: if hours is outside the 1-24 range.
        """
        if not 1 <= hours <= 24:
            raise ValueError(f"hours must be 1-24, got {hours}")
        self._write_register(_REG_RAIN_HOUR, [hours])
        return self._read_rainfall(_REG_TIME_RAINFALL)

    def reset_accumulation(self, base_mm: float = 0.0) -> None:
        """Set the cumulative rainfall base value (effectively resets the counter)."""
        raw = int(base_mm * _RAINFALL_SCALE)
        self._write_register(_REG_BASE_RAINFALL, [raw & 0xFF, raw >> 8])

    def _verify_device(self) -> bool:
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
