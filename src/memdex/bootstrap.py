"""Generating a first memory from the codebase.

When a project has no memory at all there is nothing to optimize, so Memdex
offers to write one — but only with an LLM, and only from a bounded, curated
slice of the repository (READMEs, docs, manifests, a file tree, entry-point
heads). Everything the model produces is validated before it reaches disk, and
the result is ordinary memory files that `memdex run` then maintains.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from memdex.config import MemdexConfig
from memdex.errors import LLMError, MemdexError
from memdex.identity import Registry, content_hash, dedupe_key, slugify
from memdex.layout import render_doc
from memdex.llmclient import LLMClient
from memdex.models import CATEGORIES, MemoryUnit, SourceMode
from memdex.project import SKIP_DIRS
from memdex.tokens import get_counter
from memdex.util import atomic_write

Progress = Callable[[str], None]
NOOP: Progress = lambda message: None  # noqa: E731

DOC_NAMES = ("README", "readme", "Readme", "CONTRIBUTING", "ARCHITECTURE", "DESIGN")
MANIFESTS = (
    "pyproject.toml", "package.json", "go.mod", "Cargo.toml", "pom.xml", "Gemfile",
    "composer.json", "mix.exs", "requirements.txt", "docker-compose.yml", "Makefile",
)
ENTRY_HINTS = ("main", "app", "index", "server", "cli", "__init__")
HEAD_LINES = 60
MAX_TREE_ENTRIES = 300

SYSTEM = (
    "You write durable project memory for an AI coding assistant. "
    "Reply with JSON only: {\"memories\":[{\"title\":str,\"category\":str,\"body\":str,"
    "\"importance\":number}]}. "
    f"category must be one of: {', '.join(CATEGORIES)}. "
    "Write 6 to 15 memories. Each body is concise markdown (3-12 lines) capturing "
    "architecture, decisions, conventions, commands, dependencies or structure that a "
    "developer would need to know. State only what the provided context supports — "
    "never guess at technologies or decisions that are not evidenced. No preamble."
)


@dataclass
class RepoContext:
    text: str
    files_read: int
    tokens: int


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if set(rel.parts) & SKIP_DIRS or any(p.startswith(".") for p in rel.parts[:-1]):
            continue
        if path.is_file():
            yield path


def _head(path: Path, lines: int = HEAD_LINES) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return "".join(next(handle, "") for _ in range(lines)).strip()
    except OSError:
        return ""


def gather_context(cfg: MemdexConfig) -> RepoContext:
    """Assemble a token-budgeted description of the repository."""
    counter = get_counter()
    budget = max(2000, cfg.llm.bootstrap_context_tokens)
    root = cfg.root
    chunks: list[str] = []
    used = 0
    files_read = 0

    def add(title: str, text: str) -> bool:
        nonlocal used, files_read
        if not text.strip():
            return True
        block = f"\n## {title}\n\n{text.strip()}\n"
        cost = counter.count(block)
        if used + cost > budget:
            return False
        chunks.append(block)
        used += cost
        files_read += 1
        return True

    tree: list[str] = []
    for path in _iter_files(root):
        rel = path.relative_to(root)
        if len(tree) < MAX_TREE_ENTRIES:
            tree.append(str(rel))
    add("File tree (truncated)", "\n".join(tree))

    for path in _iter_files(root):
        if path.stem in DOC_NAMES and path.suffix.lower() in (".md", ".rst", ".txt", ""):
            if not add(str(path.relative_to(root)), _head(path, 200)):
                break

    for path in _iter_files(root):
        if path.name in MANIFESTS:
            if not add(str(path.relative_to(root)), _head(path, 80)):
                break

    for path in _iter_files(root):
        if path.suffix.lower() in (".md", ".rst") and "doc" in str(path.parent).lower():
            if not add(str(path.relative_to(root)), _head(path, 80)):
                break

    for path in _iter_files(root):
        if path.stem in ENTRY_HINTS and path.suffix.lower() in (
            ".py", ".js", ".ts", ".go", ".rs", ".java", ".rb",
        ):
            if not add(str(path.relative_to(root)), _head(path)):
                break

    return RepoContext(text="\n".join(chunks), files_read=files_read, tokens=used)


def validate_memories(data: dict) -> list[dict]:
    """Keep only well-formed entries; a malformed one is dropped, not guessed at."""
    out: list[dict] = []
    for item in data.get("memories") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        if not title or not body:
            continue
        category = item.get("category")
        importance = item.get("importance")
        out.append(
            {
                "title": title[:80],
                "body": body,
                "category": category if category in CATEGORIES else "general",
                "importance": (
                    round(min(0.95, max(0.05, float(importance))), 2)
                    if isinstance(importance, (int, float))
                    else 0.6
                ),
            }
        )
    return out


def generate_seed_memory(
    cfg: MemdexConfig,
    dry_run: bool = False,
    progress: Progress = NOOP,
    client: LLMClient | None = None,
) -> int:
    from memdex import output

    progress("Reading the codebase")
    context = gather_context(cfg)
    if not context.text.strip():
        raise MemdexError(
            "There was nothing to read in this project.",
            hint="Bootstrap works from READMEs, manifests and entry points.",
        )
    progress(f"Collected context from {context.files_read} file(s) (~{context.tokens} tokens)")

    client = client or LLMClient(cfg.llm)
    user = (
        f"Project: {cfg.root.name}\n"
        f"Write the project memory an AI coding assistant should keep.\n\n{context.text}"
    )
    try:
        data = client.chat_json(SYSTEM, user)
    except LLMError as exc:
        raise MemdexError(
            exc.message,
            hint=exc.hint or "Check that the model is running, then re-run `memdex bootstrap`.",
        ) from exc

    memories = validate_memories(data)
    if not memories:
        raise MemdexError(
            "The LLM did not return any usable memories.",
            hint="Try a larger model, or write a first MEMORY.md by hand and run `memdex run`.",
        )
    progress(f"Generated {len(memories)} seed memories")

    counter = get_counter()
    registry = Registry.load(cfg.metadata_dir)
    units: list[MemoryUnit] = []
    for entry in memories:
        unit = MemoryUnit(
            title=entry["title"],
            body=entry["body"],
            source_file=cfg.rel_output_dir / entry["category"] / f"{slugify(entry['title'])}.md",
            source_mode=SourceMode.MANAGED,
            category=entry["category"],
            importance=entry["importance"],
            provenance=f"bootstrap ({cfg.llm.provider}:{cfg.llm.model})",
        )
        unit.token_count = counter.count(unit.body)
        unit.content_hash = content_hash(unit.body)
        unit.dedupe_key = dedupe_key(unit.body)
        units.append(unit)
    registry.adopt(units)

    if dry_run:
        for unit in units:
            output.info(f"  [head]{unit.title}[/head]  [dim]{unit.category}[/dim]")
            output.note(f"    {unit.body.splitlines()[0][:100]}")
        return len(units)

    targets: dict[Path, MemoryUnit] = {}
    for unit in units:
        path = cfg.root / unit.source_file
        suffix = 2
        while path in targets:
            path = path.with_name(f"{path.stem}-{suffix}.md")
            suffix += 1
        targets[path] = unit

    # Record what is about to be written, so `memdex restore` can unwind a
    # bootstrap exactly like it unwinds a run. These are all new files — the
    # manifest carries them as "created" and restore removes them.
    from memdex.backup import create_backup
    from memdex.models import FileOp

    ops = [
        FileOp(path=path.relative_to(cfg.root), action="write", reason="bootstrap seed")
        for path in targets
    ]
    create_backup(cfg, ops, reason="bootstrap")

    for path, unit in targets.items():
        atomic_write(path, render_doc(unit))

    # Leave a note in the LLM's own words about where this memory came from, so
    # a later reader knows it was generated rather than written by the team.
    atomic_write(
        cfg.metadata_dir / "bootstrap.json",
        json.dumps(
            {
                "provider": cfg.llm.provider,
                "model": cfg.llm.model,
                "memories": len(units),
                "context_tokens": context.tokens,
            },
            indent=2,
        )
        + "\n",
    )
    return len(units)
