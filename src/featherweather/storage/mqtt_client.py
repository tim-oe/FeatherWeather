"""MQTT storage client for FeatherWeather.

Publishes a WeatherPayload as JSON to the configured broker after each
sensor cycle.

Required settings.toml keys:
    MQTT_BROKER   – hostname or IP of the broker
    MQTT_PORT     – broker port (default 1883)
    MQTT_USERNAME – optional; omit or set "" to connect anonymously
    MQTT_PASSWORD – optional
    MQTT_TOPIC    – publish topic (default "featherweather/data")

Usage:
    import socketpool, wifi
    pool = socketpool.SocketPool(wifi.radio)
    client = MqttClient(pool)
    client.connect()
    client.publish(WeatherPayload(baro, temp_hum, aq, rain, wind_dir, wind_spd, illum, gps))
    client.disconnect()
"""

import os

import adafruit_minimqtt.adafruit_minimqtt as MQTT

from featherweather.storage.weather_payload import WeatherPayload

__all__ = ["MqttClient"]

_DEFAULT_PORT: int = 1883
_DEFAULT_TOPIC: str = "featherweather/data"


class MqttClient:
    """Thin wrapper around adafruit_minimqtt that publishes WeatherPayloads.

    One instance is created at startup and reused across all sensor cycles.
    Call connect() once after WiFi is up, then publish() after each cycle.
    """

    def __init__(self, socket_pool) -> None:
        """
        Args:
            socket_pool: socketpool.SocketPool bound to wifi.radio
        """
        broker: str = os.getenv("MQTT_BROKER") or ""
        port: int = int(os.getenv("MQTT_PORT") or _DEFAULT_PORT)
        username: str | None = os.getenv("MQTT_USERNAME") or None
        password: str | None = os.getenv("MQTT_PASSWORD") or None
        self._topic: str = os.getenv("MQTT_TOPIC") or _DEFAULT_TOPIC

        self._mqtt = MQTT.MQTT(
            broker=broker,
            port=port,
            username=username,
            password=password,
            socket_pool=socket_pool,
        )

    def connect(self) -> None:
        """Open the TCP connection and perform the MQTT CONNECT handshake."""
        self._mqtt.connect()

    def disconnect(self) -> None:
        """Send MQTT DISCONNECT and close the socket gracefully."""
        self._mqtt.disconnect()

    def publish(self, payload: WeatherPayload) -> None:
        """Serialise payload to JSON and publish to the configured topic.

        Args:
            payload: fully populated WeatherPayload for this sensor cycle
        """
        self._mqtt.publish(self._topic, payload.to_json())
