"""Cooperative cancellation shared by the CLI and the Qt worker thread."""

from __future__ import annotations

import threading

from .errors import GenerationCancelled


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise GenerationCancelled("Generation cancelled by the user.")
