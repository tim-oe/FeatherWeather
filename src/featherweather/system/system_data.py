"""ESP32 system resource metrics data model."""

__all__ = ["SystemData"]


class SystemData:
    """One snapshot of device resource usage.

    Attributes:
        heap_used_b:  bytes currently allocated on the MicroPython heap
        heap_free_b:  bytes available on the heap
        uptime_s:     ``time.monotonic()`` at the moment of the read
        cpu_freq_mhz: CPU clock in MHz (None if microcontroller module absent)
    """

    def __init__(self) -> None:
        self.heap_used_b: int = 0
        self.heap_free_b: int = 0
        self.uptime_s: float = 0.0
        self.cpu_freq_mhz: int | None = None

    @property
    def heap_total_b(self) -> int:
        """Total heap size in bytes (used + free)."""
        return self.heap_used_b + self.heap_free_b

    @property
    def heap_pct(self) -> int:
        """Heap utilisation as an integer percentage (0–100)."""
        total = self.heap_total_b
        return int(self.heap_used_b * 100 / total) if total else 0

    def __repr__(self) -> str:
        return (
            f"SystemData("
            f"used={self.heap_used_b // 1024} KB, "
            f"free={self.heap_free_b // 1024} KB, "
            f"pct={self.heap_pct}%, "
            f"uptime={self.uptime_s:.0f}s)"
        )
