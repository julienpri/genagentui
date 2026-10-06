"""Types partagés du Gateway (cf. VOICEGTWSPEC.md §7)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int
    channels: int
    dtype: str  # "int16" | "float32"


@dataclass(frozen=True)
class AudioChunk:
    pcm: bytes
    fmt: AudioFormat
    t_mono: float  # time.monotonic() au moment de la capture/émission


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None
    confidence: float | None
    no_speech_prob: float | None
    avg_logprob: float | None
    compression_ratio: float | None
    audio_duration_ms: int
    stt_latency_ms: int
