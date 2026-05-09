"""GPS reader for CircuitPython (Ultimate GPS FeatherWing #3133).

The PA1616D/MTK3333 module communicates over UART at 9600 baud and emits
standard NMEA-0183 sentences. The adafruit_gps library parses GGA and RMC
sentences to expose fix quality, position, altitude, speed, and heading.

NMEA sentences enabled:
    GGA — position, altitude, fix quality, satellites, HDOP
    RMC — position, speed, heading, date/time

Usage (CircuitPython):
    import busio, board
    uart = busio.UART(board.TX, board.RX, baudrate=9600, timeout=0.1)
    reader = GpsReader(uart)

    # Block until fix (or timeout) during startup:
    fixed = reader.wait_for_fix(timeout_s=120)
    if fixed:
        data = reader.read()

    # In the main polling loop:
    reader.update()          # process incoming NMEA bytes
    data = reader.read()     # snapshot current state

Reference:
    https://github.com/adafruit/Adafruit_CircuitPython_GPS
    https://cdn.sparkfun.com/assets/parts/1/2/2/8/0/PMTK_Packet_User_Manual.pdf
"""

import time

import adafruit_gps

from featherweather.gps.gps_data import GpsData

__all__ = ["GpsReader"]

# Enable GGA and RMC sentences only (position, altitude, speed, time).
# All other sentence types disabled to reduce UART traffic.
_PMTK_SET_NMEA_OUTPUT = b"PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0"

# 1 Hz update rate (1000 ms between fixes)
_PMTK_SET_UPDATE_RATE = b"PMTK220,1000"

_WAIT_POLL_INTERVAL_S: float = 0.5


class GpsReader:
    """CircuitPython reader for the Ultimate GPS FeatherWing (adafruit #3133).

    Wraps adafruit_gps.GPS to provide the same _data / _reader interface as
    the rest of the FeatherWeather sensor modules.

    The caller is responsible for polling update() regularly (e.g. every
    200 ms) so the UART receive buffer does not overflow between reads.
    In code.py this is done by the main loop's GPS poll step.
    """

    @classmethod
    def verify(
        cls,
        uart,
        nmea_timeout_s: float = 5.0,
        fix_timeout_s: float = 30.0,
    ) -> "GpsData":
        """Verify the GPS module is alive and attempt a fix.

        Args:
            uart:           busio.UART connected to the GPS FeatherWing
            nmea_timeout_s: seconds to wait for the first parsed NMEA sentence
            fix_timeout_s:  seconds to attempt a fix after NMEA is confirmed

        Returns:
            GpsData snapshot.  has_fix reflects whether a fix was acquired.

        Raises:
            RuntimeError if no NMEA sentences are received within nmea_timeout_s
            (indicates a wiring or baud-rate problem).
        """
        reader = cls(uart)

        deadline = time.monotonic() + nmea_timeout_s
        nmea_seen = False
        while time.monotonic() < deadline:
            if reader._gps.update():
                nmea_seen = True
                break
            time.sleep(0.1)

        if not nmea_seen:
            raise RuntimeError(
                f"no NMEA sentences in {nmea_timeout_s:.0f}s — check wiring/baud rate"
            )

        deadline = time.monotonic() + fix_timeout_s
        while not reader._gps.has_fix and time.monotonic() < deadline:
            reader._gps.update()
            time.sleep(0.2)

        return reader.read()

    def __init__(self, uart, debug: bool = False) -> None:
        """
        Args:
            uart:  busio.UART at 9600 8N1 connected to the GPS FeatherWing
            debug: if True, adafruit_gps prints raw NMEA sentences to serial
        """
        self._gps = adafruit_gps.GPS(uart, debug=debug)
        self._gps.send_command(_PMTK_SET_NMEA_OUTPUT)
        self._gps.send_command(_PMTK_SET_UPDATE_RATE)

    def update(self) -> bool:
        """Process any pending NMEA bytes from the GPS UART.

        Call this at least once per second (ideally every 200 ms) to keep
        the parsed state current and avoid buffer overflows.

        Returns:
            True if a new sentence was successfully parsed this call.
        """
        return self._gps.update()

    def read(self) -> GpsData:
        """Return a snapshot of the current GPS state.

        Does NOT call update() — the caller must drive update() separately
        (e.g. from a background task) to keep the state fresh.

        Returns:
            GpsData populated from the most recently parsed NMEA sentences.
            All position fields are None and has_fix is False until a fix
            is acquired.
        """
        data = GpsData()
        data.has_fix = self._gps.has_fix
        data.fix_quality = self._gps.fix_quality or 0

        if self._gps.has_fix:
            data.latitude = self._gps.latitude
            data.longitude = self._gps.longitude
            data.altitude_m = self._gps.altitude_m
            data.satellites = self._gps.satellites
            data.track_angle_deg = self._gps.track_angle_deg
            data.speed_knots = self._gps.speed_knots
            data.timestamp_utc = self._gps.timestamp_utc
            data.hdop = self._gps.horizontal_dilution

        return data

    def wait_for_fix(self, timeout_s: float = 120.0) -> bool:
        """Poll until a GPS fix is acquired or the timeout expires.

        Blocks during startup before the main sensor loop begins, polling
        the UART at _WAIT_POLL_INTERVAL_S intervals.

        Args:
            timeout_s: maximum seconds to wait (default 120 s — cold-start
                       time for the MTK3333 is typically 15-45 s with a
                       clear sky view)

        Returns:
            True if a fix was acquired, False if the timeout was reached.
        """
        deadline = time.monotonic() + timeout_s
        while not self._gps.has_fix and time.monotonic() < deadline:
            self._gps.update()
            time.sleep(_WAIT_POLL_INTERVAL_S)
        return self._gps.has_fix
