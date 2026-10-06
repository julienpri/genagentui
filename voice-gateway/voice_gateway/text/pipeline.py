"""Relie Speakable Filter -> TTS -> Player pour une phrase (VOICEGTWSPEC.md §6.7-§6.9).

Partagé entre les orchestrateurs (main_voice.py, controller/web_session.py)
pour ne pas dupliquer la logique de streaming/instrumentation.
"""

from __future__ import annotations

import asyncio
import time

from voice_gateway.audio.player import Player
from voice_gateway.audio.resampler import resample_to
from voice_gateway.core.cancel import CancelToken
from voice_gateway.text.speakable import SpeakableFilter


async def speak_sentence(
    raw_sentence: str,
    speakable: SpeakableFilter,
    tts,
    player: Player,
    out_sample_rate: int,
    timers: dict,
) -> None:
    """Synthétise et enfile une phrase. Met à jour `timers` (dict partagé par le tour) :
    `first_tts_chunk_t`, `playback_start_t`.
    """
    text = speakable.process(raw_sentence)
    if not text:
        return

    first_chunk = True
    async for chunk in tts.synthesize(text, CancelToken()):
        if first_chunk:
            timers.setdefault("first_tts_chunk_t", time.monotonic())
            first_chunk = False

        out_pcm = resample_to(chunk.pcm, chunk.fmt, out_sample_rate)
        player.enqueue(out_pcm)

        if timers.get("playback_start_t") is None:
            # Laisse la main à la boucle asyncio jusqu'à ce que le callback
            # audio ait réellement consommé ce chunk (sinon la course est
            # perdue quand une phrase ne tient qu'en un seul chunk).
            for _ in range(200):  # ~1s max
                if player.started:
                    timers["playback_start_t"] = time.monotonic()
                    break
                await asyncio.sleep(0.005)
