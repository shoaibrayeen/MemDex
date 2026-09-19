"""Planning the memory tree on disk.

Each live memory unit becomes exactly one file at ``memory/<category>/<slug>.md``
whose path is a pure function of its final category and title, and whose bytes
are rendered deterministically — no timestamps, no run counters. That is what
makes the diff against disk empty on an unchanged re-run, and it is why the
backup/write machinery downstream can trust ``file_ops`` as the complete set of
things about to change.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from memdex.config import MemdexConfig
from memdex.identity import Registry, slugify
from memdex.models import INDEX_MARKER, FileOp, MemoryUnit, PlannedDoc, SourceFile, SourceMode
from memdex.parsing import read_memdex_frontmatter, split_frontmatter
from memdex.util import is_within


def render_doc(unit: MemoryUnit) -> str:
    """A generated memory file: identity frontmatter, title, body."""
    block = {
        "memdex": {
            "id": unit.id,
            "title": unit.title,
            "category": unit.category,
            "description": unit.description,
            "importance": round(float(unit.importance), 2),
            "source": unit.provenance,
        }
    }
    frontmatter = yaml.safe_dump(block, sort_keys=False, allow_unicode=True, width=10_000).strip()
    body = unit.body.strip("\n")
    return f"---\n{frontmatter}\n---\n# {unit.title}\n\n{body}\n"


def plan_docs(units: list[MemoryUnit], cfg: MemdexConfig) -> list[PlannedDoc]:
    """Assign output paths to managed units. Readonly units stay where they are."""
    docs: list[PlannedDoc] = []
    used: dict[Path, str] = {}
    candidates = [u for u in units if u.is_live and u.is_managed and u.id]
    # Sort so that slug collisions resolve identically on every run.
    for unit in sorted(candidates, key=lambda u: (u.category, u.title.casefold(), u.id or "")):
        base = slugify(unit.title)
        path = cfg.rel_output_dir / unit.category / f"{base}.md"
        suffix = 2
        while path in used:
            path = cfg.rel_output_dir / unit.category / f"{base}-{suffix}.md"
            suffix += 1
        used[path] = unit.id or ""
        docs.append(PlannedDoc(unit_id=unit.id or "", path=path, rendered=render_doc(unit)))
    return docs


def unit_paths(docs: list[PlannedDoc], units: list[MemoryUnit]) -> dict[str, Path]:
    """Where each live unit can be found: generated path, or its readonly source."""
    paths = {doc.unit_id: doc.path for doc in docs}
    for unit in units:
        if unit.id and unit.is_live and unit.id not in paths:
            paths[unit.id] = unit.source_file
    return paths


def _existing_generated_docs(cfg: MemdexConfig) -> dict[Path, str]:
    """Generated docs already on disk, mapped to the memory ID they carry."""
    found: dict[Path, str] = {}
    if not cfg.abs_output_dir.is_dir():
        return found
    for path in sorted(cfg.abs_output_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        data, _, _ = split_frontmatter(text)
        meta = read_memdex_frontmatter(data)
        if meta:
            found[path.resolve().relative_to(cfg.root.resolve())] = meta["id"]
    return found


def diff_ops(
    docs: list[PlannedDoc],
    index_markdown: str,
    sources: list[SourceFile],
    cfg: MemdexConfig,
    index_only: bool = False,
) -> list[FileOp]:
    """Compare the plan against what is on disk and return only real changes.

    ``index_only`` is what `memdex index` uses: it regenerates MEMORY.md without
    restructuring anything, so it must never conclude that the memory files it
    deliberately did not plan are therefore obsolete.
    """
    ops: list[FileOp] = []
    planned_paths = {doc.path for doc in docs}

    for doc in docs:
        absolute = cfg.root / doc.path
        if absolute.exists():
            try:
                current = absolute.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                current = None
            if current == doc.rendered:
                continue
            ops.append(FileOp(path=doc.path, action="overwrite", reason="memory updated"))
        else:
            ops.append(FileOp(path=doc.path, action="write", reason="new memory file"))

    index_rel = cfg.rel_index_path
    if not index_only:
        # Generated docs whose memory no longer exists (or moved to a new path).
        for path in _existing_generated_docs(cfg):
            if path not in planned_paths:
                ops.append(FileOp(path=path, action="delete", reason="memory moved or merged away"))

        # Raw managed sources whose content now lives in the memory tree.
        for source in sources:
            if source.mode is not SourceMode.MANAGED or source.is_generated_index:
                continue
            if source.path == index_rel or source.path in planned_paths:
                continue
            ops.append(
                FileOp(path=source.path, action="delete", reason="content moved into memory/")
            )

    # The index itself.
    index_abs = cfg.abs_index_path
    if index_abs.exists():
        try:
            current_index = index_abs.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            current_index = None
        if current_index != index_markdown:
            ops.append(FileOp(path=index_rel, action="overwrite", reason="index regenerated"))
    elif index_markdown:
        ops.append(FileOp(path=index_rel, action="write", reason="index created"))

    _assert_managed(ops, cfg)
    return _dedupe_ops(ops)


def _dedupe_ops(ops: list[FileOp]) -> list[FileOp]:
    seen: dict[tuple[str, str], FileOp] = {}
    for op in ops:
        seen.setdefault((str(op.path), op.action), op)
    return sorted(seen.values(), key=lambda op: (op.action, str(op.path)))


def _assert_managed(ops: list[FileOp], cfg: MemdexConfig) -> None:
    """Guard rail: Memdex must never plan a write against a readonly source."""
    managed_roots = [cfg.root / p for p in cfg.managed_roots()]
    allowed = [cfg.abs_output_dir, cfg.abs_index_path, *managed_roots]
    for op in ops:
        absolute = cfg.root / op.path
        if any(absolute == root or is_within(root, absolute) for root in allowed):
            continue
        raise AssertionError(f"Refusing to modify a non-managed path: {op.path}")


def stale_vector_ids(registry: Registry, live_ids: set[str]) -> list[str]:
    return registry.stale_ids(live_ids)


def index_has_marker(text: str) -> bool:
    return INDEX_MARKER in text[:512]
