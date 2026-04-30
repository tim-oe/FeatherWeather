"""Barometric pressure sensor reading data model."""

__all__ = ["BarometricData"]


class BarometricData:
    """Environmental readings from one BMP390 sensor poll.

    Attributes:
        pressure:           raw barometric pressure at sensor altitude (hPa)
        temperature:        temperature in °C
        sea_level_pressure: pressure normalized to sea level (hPa); None until
                            set via BarometricReader.sea_level_pressure_from_altitude()
    """

    def __init__(self) -> None:
        self.pressure: float = 0.0
        self.temperature: float = 0.0
        self.sea_level_pressure: float | None = None

    def __repr__(self) -> str:
        slp = (
            f"{self.sea_level_pressure:.2f} hPa"
            if self.sea_level_pressure is not None
            else "n/a"
        )
        return (
            f"BarometricData("
            f"pressure={self.pressure:.2f} hPa, "
            f"temp={self.temperature:.2f} C, "
            f"slp={slp})"
        )
