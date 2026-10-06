"""Fake Agent (VOICEGTWSPEC.md §4.1).

Répond "J'ai bien reçu : " + transcript, streamé mot par mot avec un
délai configurable, pour simuler un agent réel et exercer le segmenteur.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator


class FakeAgent:
    def __init__(self, word_delay_ms: int = 40) -> None:
        self.word_delay_ms = word_delay_ms

    async def respond(self, transcript: str) -> AsyncIterator[str]:
        text = f"J'ai bien reçu : {transcript}"
        words = text.split(" ")
        for i, word in enumerate(words):
            yield word + (" " if i < len(words) - 1 else "")
            await asyncio.sleep(self.word_delay_ms / 1000)
