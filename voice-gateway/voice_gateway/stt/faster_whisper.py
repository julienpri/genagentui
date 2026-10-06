"""Implémentation STT par défaut : faster-whisper / CTranslate2 (§3.2, §6.5).

Transcription par segment (pas de partiels). Exécutée hors de la boucle
asyncio via un thread dédié pour ne pas bloquer l'audio.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

from voice_gateway.core.cancel import CancelToken, Cancelled
from voice_gateway.core.types import AudioFormat, Transcript

import asyncio


class WhisperSTT:
    def __init__(self) -> None:
        self._model: WhisperModel | None = None
        self._language: str | None = None
        self._beam_size: int = 1
        self._condition_on_previous_text: bool = False

    async def initialize(self, config: dict) -> None:
        model_path = Path(config["model"])
        if not model_path.exists():
            raise RuntimeError(f"Modèle STT absent ou invalide: {model_path}")

        self._language = config.get("language", "fr")
        self._beam_size = config.get("beam_size", 1)
        self._condition_on_previous_text = config.get("condition_on_previous_text", False)
        device = config.get("device", "cpu")
        compute_type = config.get("compute_type", "int8")

        def _load() -> WhisperModel:
            return WhisperModel(str(model_path), device=device, compute_type=compute_type)

        self._model = await asyncio.to_thread(_load)

    async def transcribe(self, audio: bytes, fmt: AudioFormat, cancel: CancelToken) -> Transcript:
        if self._model is None:
            raise RuntimeError("WhisperSTT non initialisé")
        cancel.raise_if_cancelled()

        if fmt.dtype != "int16" or fmt.channels != 1:
            raise ValueError("WhisperSTT attend du PCM int16 mono (format interne)")

        audio_duration_ms = int(len(audio) / 2 / fmt.sample_rate * 1000)
        t0 = time.monotonic()

        def _run():
            float_audio = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
            segments, info = self._model.transcribe(
                float_audio,
                language=self._language,
                beam_size=self._beam_size,
                condition_on_previous_text=self._condition_on_previous_text,
            )
            return list(segments), info

        segments, info = await asyncio.to_thread(_run)

        if cancel.cancelled:
            raise Cancelled()

        latency_ms = int((time.monotonic() - t0) * 1000)

        if not segments:
            return Transcript(
                text="",
                language=info.language,
                confidence=None,
                no_speech_prob=1.0,
                avg_logprob=None,
                compression_ratio=None,
                audio_duration_ms=audio_duration_ms,
                stt_latency_ms=latency_ms,
            )

        text = " ".join(s.text.strip() for s in segments).strip()
        return Transcript(
            text=text,
            language=info.language,
            confidence=None,
            no_speech_prob=max(s.no_speech_prob for s in segments),
            avg_logprob=min(s.avg_logprob for s in segments),
            compression_ratio=max(s.compression_ratio for s in segments),
            audio_duration_ms=audio_duration_ms,
            stt_latency_ms=latency_ms,
        )

    async def shutdown(self) -> None:
        self._model = None
