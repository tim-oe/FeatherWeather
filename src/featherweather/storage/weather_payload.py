"""Typed weather payload container — single source of truth for one observation.

Holds one reading from every sensor group as explicit, typed members.
All sensor fields default to ``None`` so a ``WeatherPayload`` can be
constructed incrementally (e.g. GPS-only during startup, or with only the
sensors that are physically present).

Serialisation is fully automatic via the reflection-based serializer —
adding a field to any sensor data class makes it appear in the JSON output
with no other changes required.

Usage:
    payload = WeatherPayload(
        barometric=baro_data,
        gps=gps_data,
        last_read_s=time.monotonic(),
    )
    json_str = payload.to_json()
"""

from featherweather.gps.gps_data import GpsData
from featherweather.sensors.air_quality.air_quality_data import AirQualityData
from featherweather.sensors.barometric.barometric_data import BarometricData
from featherweather.sensors.illuminance.illuminance_data import IlluminanceData
from featherweather.sensors.microphone.microphone_data import MicrophoneData
from featherweather.sensors.rainfall.rainfall_data import RainfallData
from featherweather.sensors.temp_humidity.temp_humidity_data import TempHumidityData
from featherweather.sensors.wind_direction.wind_direction_data import WindDirectionData
from featherweather.sensors.wind_speed.wind_speed_data import WindSpeedData
from featherweather.storage.serializer import to_json

__all__ = ["WeatherPayload"]


class WeatherPayload:
    """One complete weather observation composed of typed sensor readings.

    Every field is optional so callers can construct a partial payload and
    fill in fields as sensors become available.  The display controller and
    serialiser both treat ``None`` fields as "sensor absent / not yet read".

    Attributes:
        barometric:     pressure, temperature, and sea-level pressure
        temp_humidity:  temperature and relative humidity
        air_quality:    PM1.0 / PM2.5 / PM10 concentrations
        rainfall:       cumulative rainfall, tip count, uptime, rolling window
        wind_direction: bearing in degrees, 16-point code, and compass label
        wind_speed:     speed in m/s and Beaufort description
        illuminance:    ambient light in lux
        microphone:     RMS amplitude, dBFS, and estimated dB SPL
        gps:            fix status, coordinates, altitude, and UTC timestamp
        last_read_s:    ``time.monotonic()`` timestamp of the most recent
                        completed sensor cycle
        ip_address:     device IP address when WiFi is available
    """

    def __init__(
        self,
        barometric: BarometricData | None = None,
        temp_humidity: TempHumidityData | None = None,
        air_quality: AirQualityData | None = None,
        rainfall: RainfallData | None = None,
        wind_direction: WindDirectionData | None = None,
        wind_speed: WindSpeedData | None = None,
        illuminance: IlluminanceData | None = None,
        microphone: MicrophoneData | None = None,
        gps: GpsData | None = None,
        last_read_s: float | None = None,
        ip_address: str | None = None,
    ) -> None:
        self.barometric: BarometricData | None = barometric
        self.temp_humidity: TempHumidityData | None = temp_humidity
        self.air_quality: AirQualityData | None = air_quality
        self.rainfall: RainfallData | None = rainfall
        self.wind_direction: WindDirectionData | None = wind_direction
        self.wind_speed: WindSpeedData | None = wind_speed
        self.illuminance: IlluminanceData | None = illuminance
        self.microphone: MicrophoneData | None = microphone
        self.gps: GpsData | None = gps
        self.last_read_s: float | None = last_read_s
        self.ip_address: str | None = ip_address

    def to_json(self) -> str:
        """Serialise this payload to a JSON string.

        Delegates entirely to the reflection-based serialiser — no manual
        field mapping.  New fields on any sensor data class appear
        automatically in the output.
        """
        return to_json(self)
