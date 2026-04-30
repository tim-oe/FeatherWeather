"""GPS module — Ultimate GPS FeatherWing (Adafruit #3133).

Hardware: PA1616D / MTK3333 chipset
Interface: UART (TX/RX pins), 9600 baud
Features:
    - GPS + GLONASS, 33 tracking / 99 searching channels
    - -165 dBm tracking sensitivity
    - Up to 10 Hz update rate
    - Built-in RTC with CR1220 coin cell backup
    - PPS (pulse-per-second) output on fix
"""

from featherweather.gps.gps_data import GpsData
from featherweather.gps.gps_reader import GpsReader

__all__ = ["GpsData", "GpsReader"]
