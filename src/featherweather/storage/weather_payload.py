"""Typed weather payload container.

Holds one reading from every sensor group as explicit, typed members.
Serialisation is fully automatic via the reflection-based serializer —
adding a field to any sensor data class makes it appear in the JSON output
with no other changes required.

Usage:
    payload = WeatherPayload(baro, temp_hum, aq, rain, wind_dir, wind_spd, illum, gps)
    json_str = payload.to_json()
"""

from featherweather.sensors.barometric.barometric_data import BarometricData
from featherweather.sensors.temp_humidity.temp_humidity_data import TempHumidityData
from featherweather.sensors.air_quality.air_quality_data import AirQualityData
from featherweather.sensors.rainfall.rainfall_data import RainfallData
from featherweather.sensors.wind_direction.wind_direction_data import WindDirectionData
from featherweather.sensors.wind_speed.wind_speed_data import WindSpeedData
from featherweather.sensors.illuminance.illuminance_data import IlluminanceData
from featherweather.gps.gps_data import GpsData
from featherweather.storage.serializer import to_json

__all__ = ["WeatherPayload"]


class WeatherPayload:
    """One complete weather observation composed of typed sensor readings.

    Attributes:
        barometric:     pressure, temperature, and sea-level pressure
        temp_humidity:  temperature and relative humidity
        air_quality:    PM1.0 / PM2.5 / PM10 concentrations (std + atmospheric)
        rainfall:       cumulative rainfall, tip count, uptime, and rolling window
        wind_direction: bearing in degrees, 16-point code, and compass label
        wind_speed:     speed in m/s and Beaufort description
        illuminance:    ambient light in lux
        gps:            fix status, coordinates, altitude, and UTC timestamp
    """

    def __init__(
        self,
        barometric: BarometricData,
        temp_humidity: TempHumidityData,
        air_quality: AirQualityData,
        rainfall: RainfallData,
        wind_direction: WindDirectionData,
        wind_speed: WindSpeedData,
        illuminance: IlluminanceData,
        gps: GpsData,
    ) -> None:
        self.barometric: BarometricData = barometric
        self.temp_humidity: TempHumidityData = temp_humidity
        self.air_quality: AirQualityData = air_quality
        self.rainfall: RainfallData = rainfall
        self.wind_direction: WindDirectionData = wind_direction
        self.wind_speed: WindSpeedData = wind_speed
        self.illuminance: IlluminanceData = illuminance
        self.gps: GpsData = gps

    def to_json(self) -> str:
        """Serialise this payload to a JSON string.

        Delegates entirely to the reflection-based serializer — no manual
        field mapping here.  New fields on any sensor data class appear
        automatically.
        """
        return to_json(self)
