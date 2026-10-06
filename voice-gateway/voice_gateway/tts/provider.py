"""Interface TTS (VOICEGTWSPEC.md §7)."""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from voice_gateway.core.cancel import CancelToken
from voice_gateway.core.types import AudioChunk, AudioFormat


class TTSProvider(Protocol):
    async def initialize(self, config: dict) -> None: ...

    def output_format(self) -> AudioFormat: ...

    def synthesize(self, text: str, cancel: CancelToken) -> AsyncIterator[AudioChunk]: ...

    async def shutdown(self) -> None: ...
