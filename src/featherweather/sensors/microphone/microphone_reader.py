"""I2S MEMS microphone reader for CircuitPython (Adafruit #3421, SPH0645LM4H-LB).

The SPH0645 is a 24-bit I2S microphone (18-bit precision).  Reads via the new
`audio_i2sin` module (CircuitPython PR #10990, merged after 10.2.0) which adds
`audio_i2sin.I2SIn` for Espressif and RP2040 ports.

Sensitivity (from datasheet):
    −26 dBFS at 94 dB SPL, 1 kHz sine.
    Calibration offset = 94 − (−26) = 120 dB
    → dB SPL ≈ dBFS + 120   (±3–5 dB without individual calibration)

Wiring (ESP32 Feather V2):
    BCLK  → board.D27  (GPIO27)
    LRCL  → board.D13  (GPIO13 — labeled "led" on pinout PDF)
    DOUT  → board.A2   (GPIO34, input-only)
    SEL   → GND on breakout PCB (left-channel output)

Usage:
    reader = MicrophoneReader()
    data = reader.read()
    print(data.db_spl)   # e.g. 58.3

Firmware requirement:
    Requires a CircuitPython build that includes the `audio_i2sin` module
    (added in PR #10990 — not in the 10.2.0 release).
    Download a dev/nightly build from https://circuitpython.org/board/adafruit_feather_esp32_v2/
    Once merged and released, `audio_i2sin` will be available in a stable build.

    If the module is absent (e.g. stock 10.2.0), construction raises
    NotImplementedError and code.py silently skips the mic via _try_init().
"""

import array
import math

import board

try:
    import audio_i2sin as _i2sin_mod

    _I2SIn = getattr(_i2sin_mod, "I2SIn", None)
except ImportError:
    _I2SIn = None

from featherweather.sensors.microphone.microphone_data import MicrophoneData
from featherweather.sensors.sensor_base import SensorBase
from featherweather.storage.weather_payload import WeatherPayload

__all__ = ["MicrophoneReader"]

# Signed 16-bit full scale (2^15)
_MAX_AMPLITUDE: float = 32768.0

# Practical noise floor for 16-bit audio (−96 dBFS)
_NOISE_FLOOR_DBFS: float = -96.0

# SPH0645 sensitivity offset: −26 dBFS at 94 dB SPL → offset = 120
_SENSITIVITY_OFFSET_DB: float = 120.0

# Default number of samples per measurement.
# At a typical 16 kHz I2S clock, 512 samples ≈ 32 ms — fast enough for
# near-real-time level display without blocking the main loop too long.
_DEFAULT_NUM_SAMPLES: int = 512


class MicrophoneReader(SensorBase):
    """CircuitPython sound-level reader for the SPH0645LM4H-LB MEMS microphone.

    Records a burst of I2S samples via audio_i2sin.I2SIn, removes DC offset,
    computes RMS, and converts the result to dBFS and estimated dB SPL.

    Args:
        bclk_pin:    I2S bit-clock pin       (default board.D27)
        lrcl_pin:    I2S word-select pin      (default board.D13)
        data_pin:    I2S data-in pin          (default board.A2)
        num_samples: samples per measurement  (default 512)
    """

    _VERIFY_MIN_RMS: float = 10.0  # below this the mic is likely disconnected / silent

    @classmethod
    def verify(cls) -> "MicrophoneData":
        """Instantiate, record one burst, verify non-zero samples, deinit, return data.

        Raises RuntimeError if samples are all near-zero (mic not outputting data).
        Raises on any hardware or init failure.
        """
        reader = cls()
        try:
            payload = WeatherPayload()
            reader.read(payload)
        finally:
            reader.deinit()
        data = payload.microphone
        if data is None or data.rms < cls._VERIFY_MIN_RMS:
            raise RuntimeError(
                f"mic samples near-zero (rms={data.rms if data else 0:.1f}) — check wiring/SEL pin"
            )
        return data

    def __init__(
        self,
        bclk_pin=None,
        lrcl_pin=None,
        data_pin=None,
        num_samples: int = _DEFAULT_NUM_SAMPLES,
    ) -> None:
        if _I2SIn is None:
            raise NotImplementedError(
                "audio_i2sin not available in this CircuitPython build (stock 10.2.0). "
                "Flash a nightly build from circuitpython.org once PR #10990 merges."
            )
        if bclk_pin is None:
            bclk_pin = board.D27
        if lrcl_pin is None:
            lrcl_pin = board.D13
        if data_pin is None:
            data_pin = board.A2

        self._mic = _I2SIn(bclk_pin, lrcl_pin, data_pin)
        # Reuse a single signed-16-bit buffer to avoid heap allocation each call
        self._buf = array.array("h", [0] * num_samples)
        self._n = num_samples

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self, payload: WeatherPayload) -> None:
        """Record one burst of samples and set payload.microphone.

        Args:
            payload: in-progress WeatherPayload; payload.microphone is written.
        """
        self._mic.record(self._buf, self._n)
        data = self._compute(self._buf, self._n)
        print(data)
        payload.microphone = data

    def deinit(self) -> None:
        """Release the I2S peripheral."""
        self._mic.deinit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute(buf, n: int) -> MicrophoneData:
        """Calculate RMS, dBFS, and dB SPL from a raw sample buffer.

        Subtracts the DC mean before computing RMS so that a constant bias
        (common on I2S PDM mics) does not inflate the level reading.
        """
        dc = sum(buf) / n

        sum_sq = 0.0
        for s in buf:
            diff = s - dc
            sum_sq += diff * diff
        rms = math.sqrt(sum_sq / n)

        data = MicrophoneData()
        data.rms = rms

        if rms > 0.5:
            data.db_fs = 20.0 * math.log10(rms / _MAX_AMPLITUDE)
        else:
            data.db_fs = _NOISE_FLOOR_DBFS

        data.db_spl = data.db_fs + _SENSITIVITY_OFFSET_DB
        return data
