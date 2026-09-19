"""The default embedder: MiniLM-L6-v2 running locally through ONNX.

Chroma ships this model and caches it under ~/.cache/chroma after a one-time
~80MB download. Nothing but that download ever leaves the machine — the memory
text is embedded in-process.
"""

from __future__ import annotations

from memdex.errors import EmbedderUnavailable

DIM = 384


class LocalEmbedder:
    name = "local"
    dim = DIM

    def __init__(self, model: str = "default") -> None:
        self.model = "onnx-minilm-l6-v2" if model in ("", "default") else model
        try:
            from chromadb.utils import embedding_functions

            self._fn = embedding_functions.ONNXMiniLM_L6_V2(
                preferred_providers=["CPUExecutionProvider"]
            )
            probe = self._fn(["warmup"])
        except Exception as exc:  # noqa: BLE001 - any failure means "unavailable"
            raise EmbedderUnavailable(
                f"The local embedding model could not be loaded ({exc.__class__.__name__}).",
                hint=(
                    "It is downloaded once (~80MB) on first use. Without a network connection, "
                    "set embedding.provider: hash in .memdex/config.yaml to stay fully offline."
                ),
            ) from exc
        if probe is not None and len(probe) and len(probe[0]):
            self.dim = len(probe[0])

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._fn(texts)
        return [list(map(float, vector)) for vector in vectors]
