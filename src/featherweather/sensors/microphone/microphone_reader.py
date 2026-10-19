"""I2S MEMS microphone reader for CircuitPython (Adafruit #3421, SPH0645LM4H-LB).

The SPH0645 is a 24-bit I2S microphone (18-bit precision).  Reads via the
`audioi2sin` module (CircuitPython PR #10990) which adds `audioi2sin.I2SIn`
for Espressif and RP2040 ports.

Requires CircuitPython >= 10.3.0 (stable, released 2026-08-31). Not available
in 10.2.x or earlier. Current stable for this board:
    https://circuitpython.org/board/adafruit_feather_esp32_v2/

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
    Requires a CircuitPython build that includes `audioi2sin`
    (CircuitPython >= 10.3.0). Not in 10.2.x or earlier.

    If the module is absent, construction raises NotImplementedError and
    code.py silently skips the mic via _try_init().
"""

import array
import math
import time

import board

try:
    import audioi2sin as _i2sin_mod

    _I2SIn = getattr(_i2sin_mod, "I2SIn", None)
except ImportError:
    _I2SIn = None

from featherweather.sensors.microphone.microphone_data import MicrophoneData
from featherweather.sensors.sensor_base import SensorBase
from featherweather.storage.weather_payload import WeatherPayload

__all__ = ["MicrophoneReader"]

# I2S bus configuration for the SPH0645LM4H.
#
# The mic emits 24-bit samples (18 significant bits) in a 32-bit I2S slot and
# is *left-justified* (data starts on the WS edge, no one-BCLK delay).  It also
# needs BCLK >= ~1 MHz to run at all: at 16 kHz that means 32-bit stereo slots
# (16000 * 32 * 2 = 1.024 MHz).  With the audioi2sin defaults (16-bit slots,
# classic I2S alignment) BCLK is only 512 kHz and the mic never produces data,
# so record() returns all zeros.  See the audioi2sin docs: "use bit_depth=32
# ... set left_justified=True for mics like the SPH0645LM4H".
_SAMPLE_RATE: int = 16000
_BIT_DEPTH: int = 32
_LEFT_JUSTIFIED: bool = True

# Shift applied to each raw 32-bit sample to bring it to signed-16-bit scale
# before the level maths below (so _MAX_AMPLITUDE stays 2^15).
_SAMPLE_SHIFT: int = _BIT_DEPTH - 16

# Signed 16-bit full scale (2^15)
_MAX_AMPLITUDE: float = 32768.0

# ln(10) — CircuitPython's math module lacks log10(), so dB = 20*ln(x)/ln(10)
_LN10: float = math.log(10.0)

# Practical noise floor for 16-bit audio (−96 dBFS)
_NOISE_FLOOR_DBFS: float = -96.0

# SPH0645 sensitivity offset: −26 dBFS at 94 dB SPL → offset = 120
_SENSITIVITY_OFFSET_DB: float = 120.0

# Default number of samples per measurement.
# At a typical 16 kHz I2S clock, 512 samples ≈ 32 ms — fast enough for
# near-real-time level display without blocking the main loop too long.
_DEFAULT_NUM_SAMPLES: int = 512

# Warm-up after the I2S clocks start.  Measured on the SPH0645: the DC offset
# swings by several thousand counts and RMS reads 5–30x too high for ~0.8 s
# while the mic's internal high-pass filter settles.  We record and discard
# bursts for this long in __init__ so the first real reading is trustworthy.
_WARMUP_S: float = 1.0


class MicrophoneReader(SensorBase):
    """CircuitPython sound-level reader for the SPH0645LM4H-LB MEMS microphone.

    Records a burst of I2S samples via audioi2sin.I2SIn, removes DC offset,
    computes RMS, and converts the result to dBFS and estimated dB SPL.

    Args:
        bclk_pin:    I2S bit-clock pin       (default board.D27)
        lrcl_pin:    I2S word-select pin      (default board.D13)
        data_pin:    I2S data-in pin          (default board.A2)
        num_samples: samples per measurement  (default 512)
    """

    @classmethod
    def verify(cls) -> "MicrophoneData":
        """Instantiate (incl. warm-up), record one burst, check for signal, deinit.

        A connected SPH0645 always produces non-zero samples — even in silence
        there is a DC offset of a few hundred to a few thousand counts.  A
        disconnected / unpowered mic (or a floating DOUT line) yields exactly
        zero for every sample.  So the liveness test is "any non-zero raw
        sample", not an RMS threshold, which would wrongly fail in a quiet room.

        Raises RuntimeError if every sample is zero (mic not outputting data).
        Raises on any hardware or init failure.
        """
        reader = cls()
        try:
            payload = WeatherPayload()
            reader.read(payload)
            has_signal = any(reader._buf)
        finally:
            reader.deinit()
        data = payload.microphone
        if data is None or not has_signal:
            raise RuntimeError(
                "mic returned all-zero samples — check wiring (BCLK/LRCL/DOUT), "
                "3V power, and that SEL is tied to GND or 3V"
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
                "audioi2sin not available in this CircuitPython build "
                "(requires CircuitPython >= 10.3.0). "
                "Flash current stable from circuitpython.org."
            )
        if bclk_pin is None:
            bclk_pin = board.D27
        if lrcl_pin is None:
            lrcl_pin = board.D13
        if data_pin is None:
            data_pin = board.A2

        self._mic = _I2SIn(
            bclk_pin,
            lrcl_pin,
            data_pin,
            sample_rate=_SAMPLE_RATE,
            bit_depth=_BIT_DEPTH,
            mono=True,
            left_justified=_LEFT_JUSTIFIED,
        )
        # Reuse a single signed-32-bit buffer ('i' matches bit_depth=32) to
        # avoid heap allocation each call.
        self._buf = array.array("i", [0] * num_samples)
        self._n = num_samples

        # Let the mic's high-pass filter settle (see _WARMUP_S).  Recording
        # continuously (rather than sleeping) keeps the I2S DMA draining.
        t0 = time.monotonic()
        while time.monotonic() - t0 < _WARMUP_S:
            self._mic.record(self._buf, self._n)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self, payload: WeatherPayload) -> None:
        """Record one burst of samples and set payload.microphone.

        Args:
            payload: in-progress WeatherPayload; payload.microphone is written.
        """
        self._mic.record(self._buf, self._n)
        payload.microphone = self._compute(self._buf, self._n)

    def deinit(self) -> None:
        """Release the I2S peripheral."""
        self._mic.deinit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute(buf, n: int, shift: int = _SAMPLE_SHIFT) -> MicrophoneData:
        """Calculate RMS, dBFS, and dB SPL from a raw sample buffer.

        Each raw sample is arithmetic-shifted right by *shift* bits to bring it
        to signed-16-bit scale (integer shift, so no float precision loss on
        large 32-bit values).  The DC mean is then subtracted before computing
        RMS so that a constant bias (common on I2S MEMS mics) does not inflate
        the level reading.
        """
        total = 0
        for s in buf:
            total += s >> shift
        dc = total / n

        sum_sq = 0.0
        for s in buf:
            diff = (s >> shift) - dc
            sum_sq += diff * diff
        rms = math.sqrt(sum_sq / n)

        data = MicrophoneData()
        data.rms = rms

        if rms > 0.5:
            # CircuitPython's math module has no log10(); use ln(x)/ln(10).
            data.db_fs = 20.0 * math.log(rms / _MAX_AMPLITUDE) / _LN10
        else:
            data.db_fs = _NOISE_FLOOR_DBFS

        data.db_spl = data.db_fs + _SENSITIVITY_OFFSET_DB
        return data
