"""Speakable Filter (VOICEGTWSPEC.md §6.7).

Transforme du texte agent (potentiellement markdown) en texte
prononçable : résume les blocs de code, retire la syntaxe markdown,
abrège chemins/URLs/hashes longs, normalise quelques symboles usuels.

Opère sur une phrase déjà segmentée (frontière de phrase déjà connue),
pas sur des chunks bruts — plus simple et suffisant tant que l'agent ne
coupe pas un bloc de code au milieu d'une phrase.
"""

from __future__ import annotations

import re

CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
HEADER_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
BOLD_ITALIC_RE = re.compile(r"(\*\*\*|\*\*|\*|___|__|_)(.+?)\1")
BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
URL_RE = re.compile(r"https?://[^\s]+")
PATH_RE = re.compile(r"(?:[\w.\-]+/){2,}[\w.\-]+")
HASH_RE = re.compile(r"\b[0-9a-f]{8,40}\b", re.IGNORECASE)


def _summarize_code_block(match: re.Match) -> str:
    lang = match.group(1) or "de code"
    n_lines = match.group(2).count("\n") + 1
    lang_label = f" {lang}" if match.group(1) else ""
    return f"J'ai écrit un bloc{lang_label} de {n_lines} lignes. "


class SpeakableFilter:
    def __init__(self, code_blocks: str = "summarize", max_path_chars: int = 30) -> None:
        if code_blocks not in ("summarize", "drop"):
            raise ValueError("code_blocks doit être 'summarize' ou 'drop'")
        self.code_blocks = code_blocks
        self.max_path_chars = max_path_chars

    def process(self, text: str) -> str:
        if self.code_blocks == "drop":
            text = CODE_BLOCK_RE.sub(" ", text)
        else:
            text = CODE_BLOCK_RE.sub(_summarize_code_block, text)

        text = INLINE_CODE_RE.sub(r"\1", text)
        text = LINK_RE.sub(r"\1", text)
        text = URL_RE.sub("un lien", text)
        text = HEADER_RE.sub("", text)
        text = BOLD_ITALIC_RE.sub(r"\2", text)
        text = BULLET_RE.sub("", text)

        text = HASH_RE.sub(lambda m: self._shorten(m.group(0)), text)
        text = PATH_RE.sub(lambda m: self._shorten(m.group(0)), text)

        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _shorten(self, s: str) -> str:
        if len(s) <= self.max_path_chars:
            return s
        return s[:6] + "…" + s[-4:]
