"""Finding memory files.

Only the configured locations are read — Memdex never sweeps every markdown file
in a repository, because a README or a changelog is documentation, not assistant
memory, and silently rewriting one would be the worst kind of surprise.
"""

from __future__ import annotations

from pathlib import Path

from memdex.config import MemdexConfig
from memdex.models import INDEX_MARKER, SourceFile, SourceMode
from memdex.parsing import is_generated_index, read_memdex_frontmatter, split_frontmatter
from memdex.project import SKIP_DIRS
from memdex.util import is_within

MEMORY_EXTENSIONS = {".md", ".markdown", ".mdc"}
MAX_FILE_BYTES = 2 * 1024 * 1024


def _iter_markdown(root: Path, target: Path, warnings: list[str]):
    """Yield markdown files under ``target`` (a file or a directory)."""
    if target.is_file():
        if target.suffix.lower() in MEMORY_EXTENSIONS:
            yield target
        return
    if not target.is_dir():
        return
    for path in sorted(target.rglob("*")):
        if path.is_dir():
            continue
        if path.suffix.lower() not in MEMORY_EXTENSIONS:
            continue
        parts = set(path.relative_to(root).parts[:-1]) if is_within(root, path) else set()
        if parts & SKIP_DIRS:
            continue
        if path.is_symlink() and not is_within(root, path):
            warnings.append(f"Skipped symlink pointing outside the project: {path.name}")
            continue
        yield path


def discover(cfg: MemdexConfig) -> tuple[list[SourceFile], list[str]]:
    """Read every configured memory file. Returns (sources, warnings)."""
    warnings: list[str] = []
    seen: dict[tuple[int, int], SourceFile] = {}

    for raw_path, mode in cfg.sources:
        target = cfg.root / raw_path
        if not target.exists():
            continue
        for path in _iter_markdown(cfg.root, target, warnings):
            if not is_within(cfg.root, path):
                continue
            rel = path.resolve().relative_to(cfg.root.resolve())
            if rel.parts and rel.parts[0] == ".memdex":
                continue
            try:
                info = path.stat()
            except OSError as exc:
                warnings.append(f"Skipped {rel} — could not read it ({exc.__class__.__name__}).")
                continue

            # Identify files by inode, not by spelling. On a case-insensitive
            # filesystem the default sources MEMORY.md and memory.md are the same
            # file, and reading it twice would let one spelling be planned for
            # deletion while the other is written.
            key = (info.st_dev, info.st_ino)
            if key in seen:
                # First configured source wins; managed beats a later readonly listing.
                continue
            try:
                if info.st_size > MAX_FILE_BYTES:
                    warnings.append(f"Skipped {rel} — larger than 2 MiB.")
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                warnings.append(f"Skipped {rel} — could not read it ({exc.__class__.__name__}).")
                continue

            frontmatter, _, _ = split_frontmatter(text)
            generated = is_generated_index(text, INDEX_MARKER)
            adopted = (
                mode is SourceMode.MANAGED
                and is_within(cfg.abs_output_dir, path)
                and read_memdex_frontmatter(frontmatter) is not None
            )
            seen[key] = SourceFile(
                path=rel,
                mode=mode,
                raw_text=text,
                frontmatter=frontmatter,
                is_generated_index=generated,
                is_adopted_doc=adopted,
            )

    sources = sorted(seen.values(), key=lambda s: (s.mode is SourceMode.READONLY, str(s.path)))
    return sources, warnings


def count_source_roots(cfg: MemdexConfig, sources: list[SourceFile]) -> int:
    """How many configured sources actually contributed memory.

    Counted from what was discovered rather than from what exists on disk, so
    two config entries naming the same file on a case-insensitive filesystem
    count once.
    """
    roots: set[str] = set()
    for source in sources:
        for raw_path, _ in cfg.sources:
            rel = Path(raw_path.rstrip("/"))
            if source.path == rel or rel in source.path.parents:
                roots.add(str(rel))
                break
    return len(roots)


def ingestible(sources: list[SourceFile]) -> list[SourceFile]:
    """Everything except the generated index, which is output, not input."""
    return [s for s in sources if not s.is_generated_index]
