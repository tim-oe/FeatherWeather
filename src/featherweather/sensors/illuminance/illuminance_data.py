"""Illuminance sensor reading data model."""

__all__ = ["IlluminanceData"]


class IlluminanceData:
    """Ambient light reading from one SEN0644 sensor poll.

    Attributes:
        lux: illuminance in lux (0.0 - 200,000.0), resolution 0.001 lux
    """

    def __init__(self) -> None:
        self.lux: float = 0.0

    def __repr__(self) -> str:
        return f"IlluminanceData(lux={self.lux:.3f})"
