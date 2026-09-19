"""Optional embedder backed by a local Ollama server."""

from __future__ import annotations

from memdex.config import MemdexConfig
from memdex.errors import EmbedderUnavailable

DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_BASE = "http://localhost:11434"


class OllamaEmbedder:
    name = "ollama"

    def __init__(self, cfg: MemdexConfig) -> None:
        configured = cfg.embedding_model
        self.model = configured if configured not in ("", "default") else DEFAULT_MODEL
        base = cfg.llm.resolved_base_url or DEFAULT_BASE
        # Ollama's native embedding endpoint sits outside the OpenAI-compatible /v1 prefix.
        self.base_url = base[: -len("/v1")] if base.endswith("/v1") else base
        self.timeout = cfg.llm.timeout
        self.dim = 0
        probe = self.embed(["warmup"])
        if probe:
            self.dim = len(probe[0])
        if not self.dim:
            raise EmbedderUnavailable(f"Ollama returned no embedding for model {self.model!r}.")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        import httpx

        try:
            response = httpx.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": texts},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise EmbedderUnavailable(
                f"Could not reach Ollama at {self.base_url} ({exc.__class__.__name__}).",
                hint="Start it with `ollama serve`, or set embedding.provider: local.",
            ) from exc

        vectors = payload.get("embeddings") or payload.get("data") or []
        if not vectors:
            raise EmbedderUnavailable(f"Ollama returned no embeddings for model {self.model!r}.")
        return [list(map(float, vector)) for vector in vectors]
