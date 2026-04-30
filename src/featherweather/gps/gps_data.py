"""GPS sensor reading data model."""

import time

__all__ = ["GpsData"]


class GpsData:
    """GPS fix snapshot from one adafruit_gps poll cycle.

    Attributes:
        latitude:        decimal degrees, positive = North (None until fix)
        longitude:       decimal degrees, positive = East  (None until fix)
        altitude_m:      altitude above mean sea level in metres (None until fix)
        has_fix:         True when a valid GPS fix is available
        fix_quality:     NMEA GGA quality indicator (0 = no fix, 1 = GPS, 2 = DGPS)
        satellites:      number of satellites used in fix (None until fix)
        track_angle_deg: true-north heading in degrees (None until moving)
        speed_knots:     ground speed in knots (None until fix)
        timestamp_utc:   UTC time of fix as time.struct_time (None until fix)
        hdop:            horizontal dilution of precision (None until fix)
    """

    def __init__(self) -> None:
        self.latitude: float | None = None
        self.longitude: float | None = None
        self.altitude_m: float | None = None
        self.has_fix: bool = False
        self.fix_quality: int = 0
        self.satellites: int | None = None
        self.track_angle_deg: float | None = None
        self.speed_knots: float | None = None
        self.timestamp_utc: time.struct_time | None = None
        self.hdop: float | None = None

    def __repr__(self) -> str:
        if not self.has_fix:
            return "GpsData(no fix)"
        ts = (
            f"{self.timestamp_utc.tm_hour:02d}:{self.timestamp_utc.tm_min:02d}:"
            f"{self.timestamp_utc.tm_sec:02d}Z"
            if self.timestamp_utc
            else "n/a"
        )
        return (
            f"GpsData("
            f"lat={self.latitude:.6f}, "
            f"lon={self.longitude:.6f}, "
            f"alt={self.altitude_m:.1f}m, "
            f"sats={self.satellites}, "
            f"hdop={self.hdop}, "
            f"utc={ts})"
        )
