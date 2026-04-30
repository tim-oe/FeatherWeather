"""Wind speed sensor reading data model."""

__all__ = ["WindSpeedData"]

# Beaufort scale thresholds in m/s
_BEAUFORT: tuple = (
    (0.3, "Calm"),
    (1.6, "Light air"),
    (3.4, "Light breeze"),
    (5.5, "Gentle breeze"),
    (8.0, "Moderate breeze"),
    (10.8, "Fresh breeze"),
    (13.9, "Strong breeze"),
    (17.2, "Near gale"),
    (20.8, "Gale"),
    (24.5, "Strong gale"),
    (28.5, "Storm"),
    (32.4, "Violent storm"),
    (float("inf"), "Hurricane"),
)


def beaufort_description(speed_ms: float) -> str:
    """Return the Beaufort scale label for a wind speed in m/s."""
    for threshold, label in _BEAUFORT:
        if speed_ms < threshold:
            return label
    return "Hurricane"


class WindSpeedData:
    """Wind speed reading from one SEN0483 sensor poll.

    Attributes:
        speed_ms: wind speed in m/s (0.0 - 32.4)
        beaufort: Beaufort scale description
    """

    def __init__(self) -> None:
        self.speed_ms: float = 0.0
        self.beaufort: str = "Calm"

    def __repr__(self) -> str:
        return f"WindSpeedData({self.speed_ms:.1f} m/s, {self.beaufort})"
