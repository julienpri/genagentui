"""Transcript Filter anti-hallucination (VOICEGTWSPEC.md §6.6).

Whisper hallucine sur le silence et le bruit, notamment en français.
Un transcript est rejeté si une des conditions configurées est vraie.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from voice_gateway.core.types import Transcript


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


@dataclass(frozen=True)
class FilterResult:
    accepted: bool
    reason: str | None = None


class TranscriptFilter:
    def __init__(
        self,
        max_no_speech_prob: float = 0.6,
        min_avg_logprob: float = -1.0,
        max_compression_ratio: float = 2.4,
        min_chars: int = 2,
        blacklist_file: str | Path | None = None,
    ) -> None:
        self.max_no_speech_prob = max_no_speech_prob
        self.min_avg_logprob = min_avg_logprob
        self.max_compression_ratio = max_compression_ratio
        self.min_chars = min_chars
        self._blacklist: set[str] = set()
        if blacklist_file is not None:
            path = Path(blacklist_file)
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self._blacklist.add(normalize(line))

    def check(self, transcript: Transcript) -> FilterResult:
        text = transcript.text.strip()

        if len(text) < self.min_chars:
            return FilterResult(False, "too_short")

        if transcript.no_speech_prob is not None and transcript.no_speech_prob > self.max_no_speech_prob:
            return FilterResult(False, "no_speech_prob")

        if transcript.avg_logprob is not None and transcript.avg_logprob < self.min_avg_logprob:
            return FilterResult(False, "avg_logprob")

        if (
            transcript.compression_ratio is not None
            and transcript.compression_ratio > self.max_compression_ratio
        ):
            return FilterResult(False, "compression_ratio")

        if normalize(text) in self._blacklist:
            return FilterResult(False, "blacklist")

        return FilterResult(True, None)
