"""Error hierarchy.

Every error surfaced to the user carries a message and an optional hint telling
them what to do next. ``cli`` renders these as ``✗ message`` plus a dim hint and
exits 1; anything else is an internal bug and only re-raised when MEMDEX_DEBUG=1.
"""

from __future__ import annotations


class MemdexError(Exception):
    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ConfigError(MemdexError):
    pass


class NotInitializedError(MemdexError):
    def __init__(
        self,
        message: str = "Memdex is not initialized in this project.",
        hint: str = "Run `memdex init` from your project root.",
    ) -> None:
        super().__init__(message, hint)


class NotACodeProjectError(MemdexError):
    pass


class SourceError(MemdexError):
    pass


class EmbedderUnavailable(MemdexError):
    pass


class StoreError(MemdexError):
    pass


class LLMError(MemdexError):
    pass


class BackupError(MemdexError):
    pass


class LockedError(MemdexError):
    pass
