"""Rendering MEMORY.md — the index, and only the index.

This file is the one thing an assistant loads unconditionally, so it holds
titles, one-line descriptions and paths, and never a line of the memory itself.
Everything else is retrieved on demand through `memdex search`.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from memdex.config import MemdexConfig
from memdex.models import CATEGORIES, INDEX_MARKER, MemoryUnit
from memdex.tokens import TokenCounter
from memdex.util import fmt_int

HEADER = (
    f"{INDEX_MARKER} — do not edit by hand; run `memdex index` to regenerate -->\n"
    "# Memory Index\n"
)

USAGE_NOTE = (
    "> Read this index first, then open only the memories you need — "
    "or run `memdex search \"<question>\"` to find them semantically."
)


def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]").strip()


def render_index(
    units: list[MemoryUnit],
    paths: dict[str, Path],
    cfg: MemdexConfig,
    counter: TokenCounter,
) -> str:
    live = [u for u in units if u.is_live and u.id]
    if not live:
        return ""

    total_tokens = sum(u.token_count for u in live)
    approx = "~" if not counter.exact else ""
    estimated = " (estimated)" if not counter.exact else ""

    lines = [HEADER]
    lines.append(
        f"> {len(live)} memories · {approx}{fmt_int(total_tokens)} tokens of memory indexed"
        f"{estimated}.\n{USAGE_NOTE}\n"
    )

    grouped: dict[str, list[MemoryUnit]] = {}
    for unit in _collapse_readonly_files(live):
        grouped.setdefault(unit.category, []).append(unit)

    for category in CATEGORIES:
        bucket = grouped.get(category)
        if not bucket:
            continue
        bucket.sort(key=lambda u: (-u.importance, u.title.casefold(), u.id or ""))
        lines.append(f"## {category.capitalize()}\n")
        for unit in bucket:
            path = paths.get(unit.id or "", unit.source_file)
            entry = f"- [{_escape(unit.title)}]({path.as_posix()})"
            if unit.description:
                entry += f" — {unit.description}"
            lines.append(entry)
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _collapse_readonly_files(units: list[MemoryUnit]) -> list[MemoryUnit]:
    """One index entry per readonly file, not one per section.

    Memdex cannot split a readonly file, so every section of it would link to the
    same path — five lines telling a reader to open the same document. Search
    still returns the individual sections; the index points at the file once.
    """
    out: list[MemoryUnit] = []
    seen: dict[str, MemoryUnit] = {}
    for unit in units:
        if unit.is_managed:
            out.append(unit)
            continue
        key = str(unit.source_file)
        current = seen.get(key)
        # The earliest surviving section carries the file's opening sentence.
        if current is None or unit.line_start < current.line_start:
            seen[key] = unit

    for unit in seen.values():
        entry = replace(unit, title=unit.document_title or unit.title)
        out.append(entry)
    return out


def index_token_count(markdown: str, counter: TokenCounter) -> int:
    return counter.count(markdown)
