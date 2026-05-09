"""Microphone reading data model."""

__all__ = ["MicrophoneData"]


class MicrophoneData:
    """One sound level measurement from the SPH0645LM4H-LB MEMS microphone.

    Attributes:
        rms:       root-mean-square sample amplitude (0 – 32767 for 16-bit)
        db_fs:     level relative to digital full scale (dBFS; always ≤ 0)
        db_spl:    estimated sound pressure level (dB SPL)
                   Derived from the SPH0645 sensitivity spec: −26 dBFS at
                   94 dB SPL → offset = 120 dB.  Accuracy is ±3–5 dB without
                   individual calibration.
    """

    def __init__(self) -> None:
        self.rms: float = 0.0
        self.db_fs: float = -96.0
        self.db_spl: float = 24.0  # 120 + (−96) floor ≈ 24 dB SPL

    def __repr__(self) -> str:
        return (
            f"MicrophoneData("
            f"rms={self.rms:.1f}, "
            f"db_fs={self.db_fs:.1f} dBFS, "
            f"db_spl={self.db_spl:.1f} dB SPL)"
        )
