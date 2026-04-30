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
        pm_1_0_conctrt_std:     PM1.0 standard particulate matter concentration
        pm_2_5_conctrt_std:     PM2.5 standard particulate matter concentration
        pm_10_conctrt_std:      PM10  standard particulate matter concentration
        pm_1_0_conctrt_atmosph: PM1.0 atmospheric environment concentration
        pm_2_5_conctrt_atmosph: PM2.5 atmospheric environment concentration
        pm_10_conctrt_atmosph:  PM10  atmospheric environment concentration
    """

    def __init__(self) -> None:
        self.pm_1_0_conctrt_std: int = 0
        self.pm_2_5_conctrt_std: int = 0
        self.pm_10_conctrt_std: int = 0

        self.pm_1_0_conctrt_atmosph: int = 0
        self.pm_2_5_conctrt_atmosph: int = 0
        self.pm_10_conctrt_atmosph: int = 0

    def is_high(self, ceiling: int = _DEFAULT_CEILING) -> bool:
        """Return True if any channel value exceeds ceiling (likely spurious)."""
        return any(
            v > ceiling
            for v in (
                self.pm_1_0_conctrt_std,
                self.pm_2_5_conctrt_std,
                self.pm_10_conctrt_std,
                self.pm_1_0_conctrt_atmosph,
                self.pm_2_5_conctrt_atmosph,
                self.pm_10_conctrt_atmosph,
            )
        )

    def clamp_to_lower(
        self, other: "AirQualityData", ceiling: int = _DEFAULT_CEILING
    ) -> None:
        """For each channel that exceeds ceiling, keep the lower of the two readings."""
        fields = (
            "pm_1_0_conctrt_std",
            "pm_2_5_conctrt_std",
            "pm_10_conctrt_std",
            "pm_1_0_conctrt_atmosph",
            "pm_2_5_conctrt_atmosph",
            "pm_10_conctrt_atmosph",
        )
        for field in fields:
            if getattr(self, field) > ceiling:
                setattr(self, field, min(getattr(self, field), getattr(other, field)))

    def __repr__(self) -> str:
        return (
            f"AirQualityData("
            f"pm1.0={self.pm_1_0_conctrt_atmosph}, "
            f"pm2.5={self.pm_2_5_conctrt_atmosph}, "
            f"pm10={self.pm_10_conctrt_atmosph} µg/m³)"
        )
