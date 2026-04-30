"""Rainfall sensor reading data model."""

__all__ = ["RainfallData"]


class RainfallData:
    """Rainfall readings from one SEN0575 sensor poll.

    Attributes:
        cumulative_rainfall_mm: total rainfall since sensor powered on (mm)
        bucket_count:           raw tipping bucket tip count
        working_time_h:         sensor uptime in hours
        rainfall_window_mm:     rainfall in last N hours (mm); None unless
                                fetched via RainfallReader.read_window()
    """

    def __init__(self) -> None:
        self.cumulative_rainfall_mm: float = 0.0
        self.bucket_count: int = 0
        self.working_time_h: float = 0.0
        self.rainfall_window_mm: float | None = None

    def __repr__(self) -> str:
        window = (
            f"{self.rainfall_window_mm:.2f} mm"
            if self.rainfall_window_mm is not None
            else "n/a"
        )
        return (
            f"RainfallData("
            f"cumulative={self.cumulative_rainfall_mm:.2f} mm, "
            f"tips={self.bucket_count}, "
            f"uptime={self.working_time_h:.1f} h, "
            f"window={window})"
        )
