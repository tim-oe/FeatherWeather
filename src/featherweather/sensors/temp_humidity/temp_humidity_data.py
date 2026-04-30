"""Temperature and humidity sensor reading data model."""

__all__ = ["TempHumidityData"]


class TempHumidityData:
    """Environmental readings from one SHTC3 sensor poll.

    Attributes:
        temperature:       temperature in °C
        relative_humidity: relative humidity in %RH (0 - 100)
    """

    def __init__(self) -> None:
        self.temperature: float = 0.0
        self.relative_humidity: float = 0.0

    def __repr__(self) -> str:
        return (
            f"TempHumidityData("
            f"temp={self.temperature:.2f} C, "
            f"humidity={self.relative_humidity:.1f} %RH)"
        )
