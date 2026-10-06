"""Sentence Segmenter (VOICEGTWSPEC.md §6.7).

Accumule les chunks de texte en streaming et émet des phrases
complètes dès qu'elles sont terminées (ponctuation forte, ou longueur
max atteinte sur une coupure faible). Permet de commencer à parler
avant la fin de la réponse agent.
"""

from __future__ import annotations

STRONG_PUNCT = ".!?…"
WEAK_BREAK = " \t\n,;:"


class SentenceSegmenter:
    def __init__(self, max_sentence_chars: int = 220) -> None:
        self.max_sentence_chars = max_sentence_chars
        self._buf = ""

    def push(self, chunk: str) -> list[str]:
        self._buf += chunk
        sentences: list[str] = []

        while True:
            idx = self._find_strong_punct()
            if idx != -1:
                sentence = self._buf[: idx + 1].strip()
                self._buf = self._buf[idx + 1 :]
                if sentence:
                    sentences.append(sentence)
                continue

            if len(self._buf) > self.max_sentence_chars:
                cut = self._find_weak_break(self.max_sentence_chars)
                sentence = self._buf[:cut].strip()
                self._buf = self._buf[cut:]
                if sentence:
                    sentences.append(sentence)
                continue

            break

        return sentences

    def flush(self) -> str | None:
        text = self._buf.strip()
        self._buf = ""
        return text or None

    def _find_strong_punct(self) -> int:
        for i, ch in enumerate(self._buf):
            if ch in STRONG_PUNCT:
                return i
        return -1

    def _find_weak_break(self, max_chars: int) -> int:
        for i in range(max_chars, 0, -1):
            if self._buf[i - 1] in WEAK_BREAK:
                return i
        return max_chars  # coupure dure si aucune coupure faible trouvée
