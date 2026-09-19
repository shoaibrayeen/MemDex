"""Embedder protocol and fingerprinting."""

from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    name: str
    model: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def fingerprint(embedder: Embedder) -> str:
    """Identifies the vector space a collection was built in.

    Vectors from two different models are not comparable, so this string is
    stored alongside the index and any change forces a rebuild.
    """
    return f"{embedder.name}:{embedder.model}:{embedder.dim}"


def label_for(embedder: Embedder) -> str:
    if embedder.name == "local":
        return "Local"
    if embedder.name == "hash":
        return "Hash (offline)"
    return f"{embedder.name.capitalize()} ({embedder.model})"
