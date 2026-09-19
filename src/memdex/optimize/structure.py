"""Turning a parsed document into focused memory units.

Splitting happens at heading boundaries and nowhere else — a paragraph is never
cut in half. Sections are split down to their leaves, because a retrieval unit
should be one topic ("Qdrant Selection"), not one chapter ("Decisions"); the
chapter heading survives as the breadcrumb and drives categorization. Sections
too small to deserve their own file are folded back into their parent, heading
and all, so nothing is lost.
"""

from __future__ import annotations

from pathlib import Path

from memdex.config import Thresholds
from memdex.models import DocNode, MemoryUnit, SourceFile
from memdex.parsing import node_text, parse_tree, title_for_file
from memdex.tokens import TokenCounter


def _subtree_text(node: DocNode) -> str:
    return node_text(node, include_heading=False)


def _shift_headings(text: str, min_level: int = 2) -> str:
    """Re-level a merged fragment so its shallowest heading sits at H2."""
    from memdex.parsing import HEADING_RE, _fence_spans

    lines = text.splitlines()
    inside = _fence_spans(lines)
    levels = [
        len(HEADING_RE.match(line).group(1))
        for idx, line in enumerate(lines)
        if not inside[idx] and HEADING_RE.match(line)
    ]
    if not levels:
        return text
    delta = min_level - min(levels)
    if delta == 0:
        return text
    out: list[str] = []
    for idx, line in enumerate(lines):
        match = None if inside[idx] else HEADING_RE.match(line)
        if match:
            level = max(1, min(6, len(match.group(1)) + delta))
            out.append(f"{'#' * level} {(match.group(2) or '').strip()}")
        else:
            out.append(line)
    return "\n".join(out)


def _make_unit(
    source: SourceFile,
    title: str,
    body: str,
    breadcrumb: tuple[str, ...],
    node: DocNode | None,
) -> MemoryUnit:
    provenance = " > ".join([str(source.path), *breadcrumb, title]) if title else str(source.path)
    return MemoryUnit(
        title=title,
        body=_shift_headings(body.strip("\n")),
        source_file=source.path,
        source_mode=source.mode,
        breadcrumb=breadcrumb,
        line_start=node.line_start if node else 1,
        line_end=node.line_end if node else 1,
        provenance=provenance,
    )


def _units_for_node(
    node: DocNode,
    source: SourceFile,
    breadcrumb: tuple[str, ...],
    thresholds: Thresholds,
    counter: TokenCounter,
    fallback_title: str,
) -> list[MemoryUnit]:
    title = node.heading if node.heading is not None else fallback_title
    own = node.own_text.strip("\n")

    if not node.children:
        return [_make_unit(source, title, own, breadcrumb, node)] if own.strip() else []

    # The synthetic document root contributes no breadcrumb of its own.
    child_breadcrumb = (*breadcrumb, title) if node.heading is not None else breadcrumb
    bucket_parts: list[str] = [own] if own.strip() else []
    substantial: list[DocNode] = []

    for child in node.children:
        text = node_text(child, include_heading=True)
        if counter.count(text) < thresholds.min_unit_tokens:
            bucket_parts.append(text)
        else:
            substantial.append(child)

    units: list[MemoryUnit] = []
    for child in substantial:
        units.extend(
            _units_for_node(child, source, child_breadcrumb, thresholds, counter, fallback_title)
        )

    bucket = "\n\n".join(part for part in bucket_parts if part.strip()).strip("\n")
    if bucket:
        if counter.count(bucket) >= thresholds.min_unit_tokens or not units:
            units.insert(0, _make_unit(source, title, bucket, breadcrumb, node))
        else:
            # Too small to stand alone: fold it onto the first real unit so that
            # no sentence of the user's memory silently disappears.
            first = units[0]
            first.body = f"{_shift_headings(bucket)}\n\n{first.body}".strip("\n")
    return units


# Words that mark a top-level heading as the document's title rather than a
# topic. "# Project Memory" is a cover page; "# Architecture" is a real section
# whose name genuinely describes everything beneath it.
TITLE_WORDS = (
    "memory", "memories", "notes", "note", "context", "index", "readme",
    "agreement", "agreements", "instruction", "instructions", "guideline",
    "guidelines", "knowledge", "overview", "documentation", "docs", "about",
)


def looks_like_document_title(heading: str, file_stem: str) -> bool:
    lowered = heading.casefold()
    if lowered == file_stem.casefold().replace("-", " ").replace("_", " "):
        return True
    return any(word in lowered.split() for word in TITLE_WORDS)


def unwrap_document_title(root: DocNode, file_stem: str) -> tuple[DocNode, str | None]:
    """Treat a lone top-level heading as the document's title, not a category.

    Many memory files open with a single ``# Project Memory`` wrapping
    everything. Carrying that heading in every breadcrumb makes "project" a
    feature of every memory in the file, which is enough to drag unrelated notes
    into the projects category. A sole ``# Architecture``, by contrast, really is
    the context for what follows, so it stays.
    """
    if (
        len(root.children) == 1
        and not root.own_text.strip()
        and root.children[0].children
        and root.children[0].heading
        and looks_like_document_title(root.children[0].heading, file_stem)
    ):
        only = root.children[0]
        unwrapped = DocNode(
            level=0,
            heading=None,
            own_text=only.own_text,
            children=only.children,
            line_start=only.line_start,
            line_end=only.line_end,
        )
        return unwrapped, only.heading
    return root, None


def tree_to_units(
    source: SourceFile,
    root: DocNode,
    thresholds: Thresholds,
    counter: TokenCounter,
) -> list[MemoryUnit]:
    stem = Path(source.path).stem
    file_title = document_title_of(root, source)
    root, unwrapped = unwrap_document_title(root, stem)
    fallback_title = unwrapped or title_for_file(Path(source.path), source.frontmatter)

    units = _units_for_node(root, source, (), thresholds, counter, fallback_title)
    for unit in units:
        unit.document_title = file_title
    return units


def document_title_of(root: DocNode, source: SourceFile) -> str:
    """What this file as a whole is called: its sole top heading, or its name."""
    headings = [child for child in root.children if child.heading]
    if len(headings) == 1 and not root.own_text.strip():
        return headings[0].heading or ""
    return title_for_file(Path(source.path), source.frontmatter)


def units_from_text(
    source: SourceFile,
    body: str,
    thresholds: Thresholds,
    counter: TokenCounter,
) -> list[MemoryUnit]:
    return tree_to_units(source, parse_tree(body), thresholds, counter)


def split_oversized(
    unit: MemoryUnit,
    thresholds: Thresholds,
    counter: TokenCounter,
) -> list[MemoryUnit]:
    """Split a unit that outgrew the size budget, if it has internal headings.

    Locked units (files Memdex already generated) are never re-split: their IDs
    are cited elsewhere, and churning them would break re-run idempotency.
    """
    if unit.frontmatter_locked:
        return [unit]
    if counter.count(unit.body) <= thresholds.max_file_tokens:
        return [unit]

    root = parse_tree(unit.body)
    if not root.children:
        return [unit]

    source = SourceFile(path=unit.source_file, mode=unit.source_mode, raw_text="")
    child_breadcrumb = (*unit.breadcrumb, unit.title)
    pieces = _units_for_node(root, source, child_breadcrumb, thresholds, counter, unit.title)
    if len(pieces) <= 1:
        return [unit]

    for piece in pieces:
        piece.category = unit.category
        piece.importance = unit.importance
        piece.line_start = unit.line_start
        piece.line_end = unit.line_end
        if piece.title == unit.title:
            piece.id = unit.id  # the leading fragment keeps the original identity
    return pieces
