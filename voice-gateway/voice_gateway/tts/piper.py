"""Implémentation TTS par défaut : Piper (§3.2, §6.8)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import AsyncIterator

from piper import PiperVoice
from piper.config import SynthesisConfig

from voice_gateway.core.cancel import CancelToken, Cancelled
from voice_gateway.core.types import AudioChunk, AudioFormat


class PiperTTS:
    def __init__(self) -> None:
        self._voice: PiperVoice | None = None
        self._syn_config: SynthesisConfig | None = None
        self._fmt: AudioFormat | None = None

    async def initialize(self, config: dict) -> None:
        model_path = Path(config["model"])
        if not model_path.exists():
            raise RuntimeError(f"Modèle TTS absent ou invalide: {model_path}")

        speed = config.get("speed", 1.0)
        self._syn_config = SynthesisConfig(length_scale=1.0 / speed)

        def _load() -> PiperVoice:
            return PiperVoice.load(str(model_path))

        self._voice = await asyncio.to_thread(_load)
        self._fmt = AudioFormat(sample_rate=self._voice.config.sample_rate, channels=1, dtype="int16")

    def output_format(self) -> AudioFormat:
        if self._fmt is None:
            raise RuntimeError("PiperTTS non initialisé")
        return self._fmt

    async def synthesize(self, text: str, cancel: CancelToken) -> AsyncIterator[AudioChunk]:
        if self._voice is None or self._fmt is None:
            raise RuntimeError("PiperTTS non initialisé")
        cancel.raise_if_cancelled()

        def _run() -> list[bytes]:
            return [c.audio_int16_bytes for c in self._voice.synthesize(text, self._syn_config)]

        pcm_chunks = await asyncio.to_thread(_run)

        for pcm in pcm_chunks:
            if cancel.cancelled:
                raise Cancelled()
            yield AudioChunk(pcm=pcm, fmt=self._fmt, t_mono=time.monotonic())

    async def shutdown(self) -> None:
        self._voice = None
