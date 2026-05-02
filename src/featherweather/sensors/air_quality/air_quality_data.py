"""Air quality sensor reading data model.

Holds both standard (industrial) and atmospheric PM concentration values
for the three particle size channels reported by the HM3301.

Ported from https://github.com/tim-oe/WeatherWatch
(weatherwatch/sensor/aqi/Hm3301Data.py)
"""

__all__ = ["AirQualityData"]

# Values above this threshold are considered out-of-range / spurious
_DEFAULT_CEILING: int = 500


class AirQualityData:
    """PM concentration readings from one HM3301 sensor poll.

    All concentration values are in µg/m³.

    Attributes:
        pm_1_0_std: PM1.0 standard particulate matter concentration (µg/m³)
        pm_2_5_std: PM2.5 standard particulate matter concentration (µg/m³)
        pm_10_std:  PM10  standard particulate matter concentration (µg/m³)
        pm_1_0_atm: PM1.0 atmospheric environment concentration (µg/m³)
        pm_2_5_atm: PM2.5 atmospheric environment concentration (µg/m³)
        pm_10_atm:  PM10  atmospheric environment concentration (µg/m³)
    """

    def __init__(self) -> None:
        self.pm_1_0_std: int = 0
        self.pm_2_5_std: int = 0
        self.pm_10_std: int = 0

        self.pm_1_0_atm: int = 0
        self.pm_2_5_atm: int = 0
        self.pm_10_atm: int = 0

    def is_high(self, ceiling: int = _DEFAULT_CEILING) -> bool:
        """Return True if any channel value exceeds ceiling (likely spurious)."""
        return any(
            v > ceiling
            for v in (
                self.pm_1_0_std,
                self.pm_2_5_std,
                self.pm_10_std,
                self.pm_1_0_atm,
                self.pm_2_5_atm,
                self.pm_10_atm,
            )
        )

    def clamp_to_lower(
        self, other: "AirQualityData", ceiling: int = _DEFAULT_CEILING
    ) -> None:
        """For each channel that exceeds ceiling, keep the lower of the two readings."""
        fields = (
            "pm_1_0_std",
            "pm_2_5_std",
            "pm_10_std",
            "pm_1_0_atm",
            "pm_2_5_atm",
            "pm_10_atm",
        )
        for field in fields:
            if getattr(self, field) > ceiling:
                setattr(self, field, min(getattr(self, field), getattr(other, field)))

    def __repr__(self) -> str:
        return (
            f"AirQualityData("
            f"pm1.0={self.pm_1_0_atm}, "
            f"pm2.5={self.pm_2_5_atm}, "
            f"pm10={self.pm_10_atm} µg/m³)"
        )
