"""Player (VOICEGTWSPEC.md §6.9).

File de chunks consommée par le callback de sortie d'AudioIO. clear()
vide la file et le buffer en cours en moins d'une frame. Le PCM enfilé
doit déjà être au format de sortie d'AudioIO (resamplé en amont).
"""

from __future__ import annotations

import threading
from collections import deque


class Player:
    def __init__(self) -> None:
        self._chunks: deque[bytes] = deque()
        self._lock = threading.Lock()
        self._started = threading.Event()

    def enqueue(self, pcm: bytes) -> None:
        if not pcm:
            return
        with self._lock:
            self._chunks.append(pcm)

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
        self._started.clear()

    def is_idle(self) -> bool:
        with self._lock:
            return len(self._chunks) == 0

    @property
    def started(self) -> bool:
        return self._started.is_set()

    def pull(self, n_bytes: int) -> bytes:
        """Appelé depuis le callback audio (thread PortAudio) : jamais de blocage."""
        out = bytearray()
        with self._lock:
            while len(out) < n_bytes and self._chunks:
                head = self._chunks[0]
                take = head[: n_bytes - len(out)]
                out += take
                if len(take) < len(head):
                    self._chunks[0] = head[len(take) :]
                else:
                    self._chunks.popleft()
        if out:
            self._started.set()
        if len(out) < n_bytes:
            out += bytes(n_bytes - len(out))  # silence si file vide (jamais de blocage)
        return bytes(out)
