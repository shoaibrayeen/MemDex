"""Token counting.

tiktoken gives exact counts for the cl100k_base vocabulary most assistants use,
but it downloads its BPE file on first use — so an installed-but-uncached
tiktoken on an offline machine must still fall back. Every number derived from a
counter with ``exact=False`` is labelled "estimated" wherever it is rendered.
"""

from __future__ import annotations

from typing import Protocol


class TokenCounter(Protocol):
    name: str
    exact: bool

    def count(self, text: str) -> int: ...


class HeuristicCounter:
    """~4 characters per token, the usual rule of thumb for English + code."""

    name = "heuristic"
    exact = False

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)


class TiktokenCounter:
    name = "tiktoken:cl100k_base"
    exact = True

    def __init__(self, encoding) -> None:  # noqa: ANN001 - external type
        self._encoding = encoding

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._encoding.encode(text, disallowed_special=()))


_cached: TokenCounter | None = None


def get_counter(force_heuristic: bool = False) -> TokenCounter:
    global _cached
    if force_heuristic:
        return HeuristicCounter()
    if _cached is not None:
        return _cached
    counter: TokenCounter
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        encoding.encode("warmup")  # may hit the network; failure -> heuristic
        counter = TiktokenCounter(encoding)
    except Exception:
        counter = HeuristicCounter()
    _cached = counter
    return counter


def reset_counter_cache() -> None:
    """Test hook: drop the memoized counter."""
    global _cached
    _cached = None
