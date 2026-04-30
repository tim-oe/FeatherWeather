"""Wind direction sensor reading data model."""

__all__ = ["WindDirectionData"]

# Mapping of 16-direction code (0-15) to compass label
DIRECTION_LABELS: tuple = (
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
)


class WindDirectionData:
    """Wind direction reading from one SEN0482 sensor poll.

    Attributes:
        degrees:         wind direction in degrees (0.0 - 360.0), 0 = true north
        direction_code:  16-point compass code (0 = N, 4 = E, 8 = S, 12 = W)
        direction_label: human-readable compass label e.g. "NNE"
    """

    def __init__(self) -> None:
        self.degrees: float = 0.0
        self.direction_code: int = 0
        self.direction_label: str = "N"

    def __repr__(self) -> str:
        return f"WindDirectionData({self.degrees:.1f} deg, {self.direction_label})"
