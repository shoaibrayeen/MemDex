"""Deterministic feature-hashing embedder — the one that always works.

No model, no download, no network: word and bigram features are hashed into a
fixed number of buckets and L2-normalized. Retrieval quality is well below a
trained model's, but it is genuinely offline, byte-stable across machines and
Python versions, and it makes air-gapped installs and the test suite possible.
Its dimensionality matches the local MiniLM model so that a fallback never
trips a dimension mismatch before the fingerprint check catches it.
"""

from __future__ import annotations

import hashlib
import math
import re

TOKEN_RE = re.compile(r"[a-z0-9]+")
DIM = 384


class HashEmbedder:
    name = "hash"
    model = "feature-hash-v1"
    dim = DIM

    def _features(self, text: str) -> list[str]:
        words = TOKEN_RE.findall(text.casefold())
        features = list(words)
        features.extend(f"{a}_{b}" for a, b in zip(words, words[1:], strict=False))
        return features

    def _vector(self, text: str) -> list[float]:
        buckets = [0.0] * self.dim
        for feature in self._features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self.dim
            sign = 1.0 if value >> 63 & 1 else -1.0
            buckets[index] += sign
        norm = math.sqrt(sum(value * value for value in buckets))
        if norm == 0.0:
            # An empty or symbol-only memory still needs a valid unit vector.
            buckets[0] = 1.0
            return buckets
        return [value / norm for value in buckets]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]
