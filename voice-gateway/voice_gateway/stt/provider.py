"""Interface STT (VOICEGTWSPEC.md §7). Le coeur du Gateway n'importe que ce Protocol."""

from __future__ import annotations

from typing import Protocol

from voice_gateway.core.cancel import CancelToken
from voice_gateway.core.types import AudioFormat, Transcript


class STTProvider(Protocol):
    async def initialize(self, config: dict) -> None: ...

    async def transcribe(self, audio: bytes, fmt: AudioFormat, cancel: CancelToken) -> Transcript: ...

    async def shutdown(self) -> None: ...
