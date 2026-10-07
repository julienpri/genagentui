"""Implémentation TTS alternative : commande macOS `say` (§3.2, alternative à Piper).

`say` produit directement du PCM 16 bits via --data-format=LEI16@<rate>,
lisible avec le module stdlib `wave` — pas de parsing AIFF, pas de
nouvelle dépendance.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import AsyncIterator

from voice_gateway.core.cancel import CancelToken, Cancelled
from voice_gateway.core.types import AudioChunk, AudioFormat


class SayTTS:
    def __init__(self) -> None:
        self._voice: str | None = None
        self._sample_rate: int = 22050
        self._fmt: AudioFormat | None = None

    async def initialize(self, config: dict) -> None:
        self._voice = config.get("voice")  # None = voix système par défaut
        self._sample_rate = config.get("sample_rate", 22050)
        self._fmt = AudioFormat(sample_rate=self._sample_rate, channels=1, dtype="int16")

        def _check() -> None:
            if subprocess.run(["which", "say"], capture_output=True).returncode != 0:
                raise RuntimeError("Commande 'say' introuvable (macOS uniquement)")

        await asyncio.to_thread(_check)

    def output_format(self) -> AudioFormat:
        if self._fmt is None:
            raise RuntimeError("SayTTS non initialisé")
        return self._fmt

    async def synthesize(self, text: str, cancel: CancelToken) -> AsyncIterator[AudioChunk]:
        if self._fmt is None:
            raise RuntimeError("SayTTS non initialisé")
        cancel.raise_if_cancelled()

        def _run() -> bytes:
            with tempfile.TemporaryDirectory() as tmp_dir:
                wav_path = Path(tmp_dir) / "say.wav"
                cmd = ["say", "-o", str(wav_path), "--data-format", f"LEI16@{self._sample_rate}"]
                if self._voice:
                    cmd += ["-v", self._voice]
                cmd.append(text)
                subprocess.run(cmd, check=True, capture_output=True)
                with wave.open(str(wav_path), "rb") as wf:
                    return wf.readframes(wf.getnframes())

        pcm = await asyncio.to_thread(_run)

        if cancel.cancelled:
            raise Cancelled()

        yield AudioChunk(pcm=pcm, fmt=self._fmt, t_mono=time.monotonic())

    async def shutdown(self) -> None:
        pass
