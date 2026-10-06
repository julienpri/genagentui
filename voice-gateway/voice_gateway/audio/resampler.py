"""Resampler vers le format interne du Gateway (VOICEGTWSPEC.md §6.2).

Format interne : PCM int16 mono 16 kHz. Fixe pendant une session.
"""

from __future__ import annotations

import numpy as np
import soxr

from voice_gateway.core.types import AudioFormat

INTERNAL_SAMPLE_RATE = 16000
INTERNAL_CHANNELS = 1
INTERNAL_DTYPE = "int16"


def internal_format() -> AudioFormat:
    return AudioFormat(sample_rate=INTERNAL_SAMPLE_RATE, channels=INTERNAL_CHANNELS, dtype=INTERNAL_DTYPE)


def resample_to(pcm: bytes, src_fmt: AudioFormat, dst_sample_rate: int, dst_channels: int = 1) -> bytes:
    """Convertit un buffer PCM int16 vers un samplerate/nb de canaux cible."""
    if src_fmt.dtype != "int16":
        raise ValueError(f"dtype source non supporté: {src_fmt.dtype}")
    if dst_channels != 1:
        raise ValueError("seule la sortie mono est supportée")

    audio = np.frombuffer(pcm, dtype=np.int16).reshape(-1, src_fmt.channels)

    if src_fmt.channels > 1:
        audio = audio.mean(axis=1).astype(np.int16)
    else:
        audio = audio.reshape(-1)

    if src_fmt.sample_rate != dst_sample_rate:
        float_audio = audio.astype(np.float32) / 32768.0
        resampled = soxr.resample(float_audio, src_fmt.sample_rate, dst_sample_rate)
        audio = np.clip(resampled * 32768.0, -32768, 32767).astype(np.int16)

    return audio.tobytes()


def resample_pcm(pcm: bytes, src_fmt: AudioFormat) -> bytes:
    """Convertit un buffer PCM int16 vers le format interne (16kHz mono int16)."""
    return resample_to(pcm, src_fmt, INTERNAL_SAMPLE_RATE, INTERNAL_CHANNELS)
