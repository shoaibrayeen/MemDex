"""Loading, validating and defaulting ``.memdex/config.yaml``.

Only ``version`` is required. Every other key has a default, so a hand-trimmed
config keeps working, and unknown keys are reported once rather than rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from memdex.errors import ConfigError, NotInitializedError
from memdex.models import SourceMode
from memdex.util import is_within

MEMDEX_DIR = ".memdex"
CONFIG_NAME = "config.yaml"

DEFAULT_MANAGED_SOURCES = ("MEMORY.md", "memory.md", "memory/", ".memory/")
DEFAULT_READONLY_SOURCES = ("AGENTS.md", ".claude/", ".cursor/", ".ai/", ".agent/")

PROVIDER_BASE_URLS = {
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}

EMBEDDING_PROVIDERS = ("local", "hash", "ollama")


@dataclass
class LLMConfig:
    enabled: bool = False
    provider: str = "ollama"
    model: str | None = None
    base_url: str | None = None
    timeout: float = 30.0
    bootstrap_context_tokens: int = 24000

    @property
    def resolved_base_url(self) -> str | None:
        if self.base_url:
            return self.base_url.rstrip("/")
        return PROVIDER_BASE_URLS.get(self.provider)

    @property
    def is_usable(self) -> bool:
        return bool(self.enabled and self.model and self.resolved_base_url)

    @property
    def label(self) -> str:
        return f"LLM ({self.provider}:{self.model})"


@dataclass
class Thresholds:
    duplicate_similarity: float = 0.85
    max_file_tokens: int = 1200
    min_unit_tokens: int = 20


@dataclass
class UIConfig:
    enabled: bool = True
    port: int = 7644


@dataclass
class MemdexConfig:
    root: Path
    version: int = 1
    sources: list[tuple[str, SourceMode]] = field(default_factory=list)
    index_path: str = "MEMORY.md"
    output_dir: str = "memory/"
    embedding_provider: str = "local"
    embedding_model: str = "default"
    store_provider: str = "chromadb"
    store_path: str = ".memdex/chroma"
    optimizer_mode: str = "deterministic"
    thresholds: Thresholds = field(default_factory=Thresholds)
    llm: LLMConfig = field(default_factory=LLMConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    audit_enabled: bool = True
    warnings: list[str] = field(default_factory=list)

    # -- derived paths -----------------------------------------------------
    @property
    def memdex_dir(self) -> Path:
        return self.root / MEMDEX_DIR

    @property
    def config_file(self) -> Path:
        return self.memdex_dir / CONFIG_NAME

    @property
    def chroma_dir(self) -> Path:
        return self.root / self.store_path

    @property
    def metadata_dir(self) -> Path:
        return self.memdex_dir / "metadata"

    @property
    def index_dir(self) -> Path:
        return self.memdex_dir / "index"

    @property
    def backups_dir(self) -> Path:
        return self.memdex_dir / "backups"

    @property
    def abs_index_path(self) -> Path:
        return self.root / self.index_path

    @property
    def rel_index_path(self) -> Path:
        return Path(self.index_path)

    @property
    def rel_output_dir(self) -> Path:
        return Path(self.output_dir.rstrip("/"))

    @property
    def abs_output_dir(self) -> Path:
        return self.root / self.rel_output_dir

    def managed_roots(self) -> list[Path]:
        return [Path(p.rstrip("/")) for p, mode in self.sources if mode is SourceMode.MANAGED]


def default_sources() -> list[tuple[str, SourceMode]]:
    out = [(p, SourceMode.MANAGED) for p in DEFAULT_MANAGED_SOURCES]
    out += [(p, SourceMode.READONLY) for p in DEFAULT_READONLY_SOURCES]
    return out


def _as_bool(value, fallback: bool) -> bool:  # noqa: ANN001
    return bool(value) if isinstance(value, bool) else fallback


def _as_int(value, fallback: int) -> int:  # noqa: ANN001
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _as_float(value, fallback: float) -> float:  # noqa: ANN001
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _parse_sources(raw, root: Path, warnings: list[str]) -> list[tuple[str, SourceMode]]:
    if raw is None:
        return default_sources()
    if not isinstance(raw, list):
        raise ConfigError(
            "memory.sources must be a list of paths.",
            hint="Example:\n  sources:\n    - MEMORY.md\n    - {path: .claude/, mode: readonly}",
        )
    out: list[tuple[str, SourceMode]] = []
    for item in raw:
        if isinstance(item, str):
            path, mode = item, SourceMode.MANAGED
        elif isinstance(item, dict) and "path" in item:
            path = str(item["path"])
            raw_mode = str(item.get("mode", "managed")).lower()
            if raw_mode not in ("managed", "readonly"):
                raise ConfigError(
                    f"Unknown source mode {raw_mode!r} for {path!r}.",
                    hint="Use mode: managed (Memdex may rewrite it) or readonly (index only).",
                )
            mode = SourceMode(raw_mode)
        else:
            raise ConfigError(f"Invalid entry in memory.sources: {item!r}")

        if Path(path).is_absolute() or path.startswith("~"):
            raise ConfigError(
                f"memory.sources entry {path!r} must be relative to the project root."
            )
        if not is_within(root, root / path):
            raise ConfigError(f"memory.sources entry {path!r} escapes the project root.")
        # Readonly wins if the same path is listed twice with different modes.
        existing = {p for p, _ in out}
        if path in existing:
            warnings.append(f"Duplicate source {path!r} in config; keeping the first entry.")
            continue
        out.append((path, mode))
    return out


def parse_config(data: dict | None, root: Path) -> MemdexConfig:
    data = data or {}
    if not isinstance(data, dict):
        raise ConfigError("config.yaml must contain a YAML mapping.")

    warnings: list[str] = []
    version = _as_int(data.get("version", 1), 1)
    if version != 1:
        raise ConfigError(
            f"Unsupported config version {version}.",
            hint="This build of Memdex understands version: 1.",
        )

    known_top = {
        "version", "memory", "embedding", "vector_store",
        "optimizer", "llm", "ui", "audit",
    }
    unknown = sorted(set(data) - known_top)
    if unknown:
        warnings.append(f"Ignoring unknown config keys: {', '.join(unknown)}")

    memory = data.get("memory") or {}
    output = memory.get("output") or {}
    embedding = data.get("embedding") or {}
    store = data.get("vector_store") or {}
    optimizer = data.get("optimizer") or {}
    llm_raw = data.get("llm") or {}
    ui_raw = data.get("ui") or {}
    audit_raw = data.get("audit") or {}

    sources = _parse_sources(memory.get("sources"), root, warnings)

    provider = str(embedding.get("provider", "local")).lower()
    if provider not in EMBEDDING_PROVIDERS:
        raise ConfigError(
            f"Unknown embedding provider {provider!r}.",
            hint=f"Supported: {', '.join(EMBEDDING_PROVIDERS)}.",
        )

    store_provider = str(store.get("provider", "chromadb")).lower()
    if store_provider != "chromadb":
        raise ConfigError(
            f"Unknown vector_store provider {store_provider!r}.",
            hint="Memdex stores vectors in a local ChromaDB (provider: chromadb).",
        )

    optimizer_mode = str(optimizer.get("mode", "deterministic")).lower()
    if optimizer_mode not in ("deterministic", "llm"):
        raise ConfigError(
            f"Unknown optimizer mode {optimizer_mode!r}.",
            hint="Use deterministic (default) or llm.",
        )

    thresholds = Thresholds(
        duplicate_similarity=_as_float(optimizer.get("duplicate_similarity"), 0.85),
        max_file_tokens=_as_int(optimizer.get("max_file_tokens"), 1200),
        min_unit_tokens=_as_int(optimizer.get("min_unit_tokens"), 20),
    )
    if not 0.0 < thresholds.duplicate_similarity <= 1.0:
        raise ConfigError("optimizer.duplicate_similarity must be between 0 and 1.")

    llm_model = llm_raw.get("model")
    llm = LLMConfig(
        enabled=_as_bool(llm_raw.get("enabled"), False),
        provider=str(llm_raw.get("provider", "ollama")).lower(),
        model=str(llm_model) if llm_model else None,
        base_url=str(llm_raw["base_url"]) if llm_raw.get("base_url") else None,
        timeout=_as_float(llm_raw.get("timeout"), 30.0),
        bootstrap_context_tokens=_as_int(llm_raw.get("bootstrap_context_tokens"), 24000),
    )

    index_path = str(output.get("index", "MEMORY.md"))
    output_dir = str(output.get("directory", "memory/"))
    if Path(index_path).is_absolute() or Path(output_dir).is_absolute():
        raise ConfigError("memory.output paths must be relative to the project root.")

    return MemdexConfig(
        root=root,
        version=version,
        sources=sources,
        index_path=index_path,
        output_dir=output_dir,
        embedding_provider=provider,
        embedding_model=str(embedding.get("model", "default")),
        store_provider=store_provider,
        store_path=str(store.get("path", ".memdex/chroma")),
        optimizer_mode=optimizer_mode,
        thresholds=thresholds,
        llm=llm,
        ui=UIConfig(
            enabled=_as_bool(ui_raw.get("enabled"), True),
            port=_as_int(ui_raw.get("port"), 7644),
        ),
        audit_enabled=_as_bool(audit_raw.get("enabled"), True),
        warnings=warnings,
    )


def load_config(root: Path) -> MemdexConfig:
    config_file = root / MEMDEX_DIR / CONFIG_NAME
    if not config_file.exists():
        raise NotInitializedError()
    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"Could not parse {config_file}: {exc}",
            hint="Fix the YAML, or delete .memdex/config.yaml and run `memdex init` again.",
        ) from exc
    return parse_config(data, root)


def default_config_yaml(
    *,
    ui_enabled: bool = True,
    embedding_provider: str = "local",
) -> str:
    """The commented template `memdex init` writes."""
    return f"""# Memdex configuration — everything here is local to this project.
version: 1

memory:
  # Where project memory lives. A bare string means "managed": Memdex may
  # reorganize it (always after a backup). mode: readonly means index-only —
  # Memdex reads and searches it but never rewrites it.
  sources:
    - MEMORY.md
    - memory/
    - .memory/
    - {{path: AGENTS.md, mode: readonly}}
    - {{path: .claude/, mode: readonly}}
    - {{path: .cursor/, mode: readonly}}
    - {{path: .ai/, mode: readonly}}
    - {{path: .agent/, mode: readonly}}

  output:
    index: MEMORY.md      # the small index an assistant loads first
    directory: memory/    # where the actual memory files live

embedding:
  # local = bundled MiniLM ONNX model (one-time ~80MB download, then offline)
  # hash  = pure-offline deterministic embedder, no downloads ever
  # ollama = use a local Ollama embedding model
  provider: {embedding_provider}
  model: default

vector_store:
  provider: chromadb
  path: .memdex/chroma

optimizer:
  mode: deterministic     # deterministic (default) or llm
  duplicate_similarity: 0.85
  max_file_tokens: 1200
  min_unit_tokens: 20

llm:
  enabled: false
  provider: ollama        # ollama | lmstudio | openai-compatible
  model: null             # e.g. qwen2.5:14b — prefer a high-context model
  base_url: null          # defaults per provider
  timeout: 30
  bootstrap_context_tokens: 24000

ui:
  enabled: {str(ui_enabled).lower()}
  port: 7644

audit:
  enabled: true
"""
