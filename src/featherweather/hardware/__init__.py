"""Hardware abstraction layer for FeatherWeather.

Provides singleton accessors for shared hardware buses so that sensor
readers and the display controller do not need to manage bus lifetime
themselves.  All CircuitPython-specific imports live here; the rest of
the package only imports from featherweather.hardware.
"""
