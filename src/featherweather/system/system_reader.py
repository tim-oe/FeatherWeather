"""System resource reader — collects heap and CPU metrics each sensor cycle."""

import gc
import time

from featherweather.sensors.sensor_base import SensorBase
from featherweather.storage.weather_payload import WeatherPayload
from featherweather.system.system_data import SystemData

__all__ = ["SystemReader"]


class SystemReader(SensorBase):
    """Collects ESP32 system metrics using the ``gc`` and ``microcontroller`` modules.

    ``microcontroller`` is attempted at import time; if the firmware build does
    not include it (e.g. on a host running tests) the CPU fields are left as
    ``None`` and the rest of the reading succeeds normally.
    """

    def read(self, payload: WeatherPayload) -> None:
        """Snapshot heap usage and CPU stats, writing the result to *payload*.

        Args:
            payload: the in-progress ``WeatherPayload`` for this reading cycle.
        """
        gc.collect()

        data = SystemData()
        data.heap_used_b = gc.mem_alloc()
        data.heap_free_b = gc.mem_free()
        data.uptime_s = time.monotonic()

        try:
            import microcontroller  # noqa: PLC0415

            data.cpu_freq_mhz = int(microcontroller.cpu.frequency / 1_000_000)
        except (ImportError, AttributeError):
            pass

        payload.system = data
