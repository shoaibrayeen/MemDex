"""Local embedding providers."""

from __future__ import annotations

from collections.abc import Callable

from memdex.config import MemdexConfig
from memdex.embed.base import Embedder, fingerprint
from memdex.embed.hashing import HashEmbedder

Warn = Callable[[str], None]


def get_embedder(cfg: MemdexConfig, warn: Warn | None = None) -> Embedder:
    """Build the configured embedder, falling back to the offline one.

    A run must never die because a model could not be loaded: the memory files
    are the product, the vectors are an index over them, and an index can always
    be rebuilt later with `memdex index`.
    """
    warn = warn or (lambda message: None)
    provider = cfg.embedding_provider

    if provider == "hash":
        return HashEmbedder()

    if provider == "ollama":
        from memdex.embed.ollama import OllamaEmbedder
        from memdex.errors import EmbedderUnavailable

        try:
            return OllamaEmbedder(cfg)
        except EmbedderUnavailable as exc:
            warn(f"{exc.message} Falling back to the offline hash embedder.")
            return HashEmbedder()

    from memdex.embed.local import LocalEmbedder
    from memdex.errors import EmbedderUnavailable

    try:
        return LocalEmbedder(model=cfg.embedding_model)
    except EmbedderUnavailable as exc:
        warn(f"{exc.message} Falling back to the offline hash embedder.")
        return HashEmbedder()


__all__ = ["Embedder", "HashEmbedder", "fingerprint", "get_embedder"]
