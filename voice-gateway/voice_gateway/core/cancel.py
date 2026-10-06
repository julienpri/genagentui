"""Annulation explicite (VOICEGTWSPEC.md §2, §7)."""

from __future__ import annotations

import asyncio


class Cancelled(Exception):
    pass


class CancelToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise Cancelled()
