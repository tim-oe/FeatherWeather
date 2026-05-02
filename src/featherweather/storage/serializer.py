"""Reflection-based JSON serializer for FeatherWeather data objects.

Walks an object's __dict__ recursively (like Jackson's field discovery),
converting instance attributes to a JSON-serializable structure without
any manual field mapping.  Adding a field to any data class automatically
makes it appear in the output.

Special types handled:
    time.struct_time  →  ISO-8601 UTC string ("YYYY-MM-DDTHH:MM:SSZ")

All other non-primitive types fall back to str().

Usage:
    from featherweather.storage.serializer import to_json
    json_str = to_json(weather_payload)
"""

import json
import time

__all__ = ["to_json"]


def _to_serializable(obj: object) -> object:
    """Recursively convert obj to a JSON-serializable Python primitive.

    Discovery order:
        1. JSON-native primitives  →  returned as-is
        2. time.struct_time        →  ISO-8601 UTC string
        3. Any object with __dict__ →  dict of {attr: _to_serializable(value)}
        4. Fallback                →  str(obj)
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj

    if isinstance(obj, time.struct_time):
        return (
            f"{obj.tm_year:04d}-{obj.tm_mon:02d}-{obj.tm_mday:02d}"
            f"T{obj.tm_hour:02d}:{obj.tm_min:02d}:{obj.tm_sec:02d}Z"
        )

    if hasattr(obj, "__dict__"):
        return {key: _to_serializable(val) for key, val in vars(obj).items()}

    return str(obj)


def to_json(obj: object) -> str:
    """Serialise obj to a JSON string via recursive __dict__ introspection.

    Args:
        obj: any object whose public instance attributes are JSON-compatible
             primitives, nested data objects, or time.struct_time values.

    Returns:
        Compact JSON string.
    """
    return json.dumps(_to_serializable(obj))
