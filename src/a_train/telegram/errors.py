"""Structured failure type for telegram encoding, mapped to HTTP 400 by adapters."""

from __future__ import annotations


class TelegramError(ValueError):
    """A telegram payload could not be encoded.

    ``path`` locates the offending value in the caller's JSON object using
    dotted/indexed notation, e.g. ``packets[0].gradients[2].g_a``.
    """

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}" if path else message)
