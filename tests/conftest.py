"""Shared fixtures.

Two rules hold for the whole suite: nothing touches the network (the local
embedder is stubbed out so a stray call fails loudly instead of downloading a
model), and every project fixture uses the offline hash embedder, so the tests
run identically on a laptop and in an air-gapped CI box.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memdex.config import load_config
from memdex.errors import EmbedderUnavailable

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "demo-project"

# Captured before the autouse guard below replaces it.
from memdex.embed.local import LocalEmbedder as _LocalEmbedder  # noqa: E402

REAL_LOCAL_INIT = _LocalEmbedder.__init__


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Make the downloading embedder unavailable so no test can reach out."""

    def refuse(self, *args, **kwargs):
        raise EmbedderUnavailable("Local model disabled during tests.")

    monkeypatch.setattr("memdex.embed.local.LocalEmbedder.__init__", refuse)


@pytest.fixture
def real_local_init(monkeypatch):
    """Restore the genuine LocalEmbedder constructor for tests about it.

    Its model loading is still stubbed by the caller — this only undoes the
    blanket guard above so the class's own error handling can be exercised.
    """
    from memdex.embed import local

    monkeypatch.setattr(local.LocalEmbedder, "__init__", REAL_LOCAL_INIT)
    return local.LocalEmbedder


@pytest.fixture(autouse=True)
def fresh_token_counter():
    from memdex.tokens import reset_counter_cache

    reset_counter_cache()
    yield
    reset_counter_cache()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def write_config(root: Path, **overrides: str) -> None:
    """Write a minimal config that is offline by default."""
    values = {
        "embedding_provider": "hash",
        "index": "MEMORY.md",
        "directory": "memory/",
    }
    values.update(overrides)
    (root / ".memdex").mkdir(parents=True, exist_ok=True)
    (root / ".memdex" / "config.yaml").write_text(
        f"""version: 1
memory:
  sources:
    - MEMORY.md
    - memory/
    - .memory/
    - {{path: .claude/, mode: readonly}}
  output:
    index: {values["index"]}
    directory: {values["directory"]}
embedding:
  provider: {values["embedding_provider"]}
  model: default
vector_store:
  provider: chromadb
  path: .memdex/chroma
optimizer:
  mode: deterministic
llm:
  enabled: false
ui:
  enabled: true
  port: 7644
audit:
  enabled: true
""",
        encoding="utf-8",
    )


@pytest.fixture
def demo_project(tmp_path: Path, monkeypatch) -> Path:
    """A copy of examples/demo-project, initialized and offline."""
    root = tmp_path / "demo"
    shutil.copytree(EXAMPLES, root)
    write_config(root)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def bare_project(tmp_path: Path, monkeypatch) -> Path:
    """An initialized project that looks like code but holds no memory yet."""
    root = tmp_path / "bare"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'bare'\n", encoding="utf-8")
    (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    write_config(root)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def cfg(demo_project: Path):
    return load_config(demo_project)


def tree_hash(root: Path, *paths: str) -> str:
    """A single hash over a set of files — used to prove nothing changed."""
    digest = hashlib.sha256()
    for name in paths:
        target = root / name
        candidates = sorted(target.rglob("*")) if target.is_dir() else [target]
        for path in candidates:
            if not path.is_file():
                continue
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def unit(title: str, body: str, **kwargs):
    """Build a MemoryUnit with hashes filled in, the way the pipeline would."""
    from memdex.identity import content_hash, dedupe_key
    from memdex.models import MemoryUnit

    source = kwargs.pop("source_file", Path("MEMORY.md"))
    memory = MemoryUnit(title=title, body=body, source_file=source, **kwargs)
    memory.content_hash = content_hash(body)
    memory.dedupe_key = dedupe_key(body)
    memory.token_count = max(1, len(body) // 4)
    return memory
