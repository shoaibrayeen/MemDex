"""Markdown structure: frontmatter, fence-aware heading tree.

Deliberately hand-rolled rather than pulled from a markdown library: the only
structure Memdex cares about is "where do the headings fall, and which of them
are really code", and a small state machine makes the fence rules explicit and
testable. Headings inside fenced code blocks must never split a memory, which is
exactly the bug a naive ``line.startswith('#')`` scan would ship with.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from memdex.models import CATEGORIES, DocNode, SourceFile, SourceMode
from memdex.util import humanize_stem

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?: +(.*?))?(?: +#+)? *$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _fence_spans(lines: list[str]) -> list[bool]:
    """Per line: True when the line is inside (or delimits) a fenced code block."""
    inside = [False] * len(lines)
    open_char: str | None = None
    open_len = 0
    for i, line in enumerate(lines):
        match = FENCE_RE.match(line)
        if open_char is None:
            if match:
                open_char = match.group(1)[0]
                open_len = len(match.group(1))
                inside[i] = True
            continue
        inside[i] = True
        if match and match.group(1)[0] == open_char and len(match.group(1)) >= open_len:
            # A closing fence carries no info string.
            if not match.group(2).strip():
                open_char = None
                open_len = 0
    return inside


def split_frontmatter(text: str) -> tuple[dict | None, str, str]:
    """Return (parsed frontmatter, raw frontmatter block, remaining body).

    A ``---`` only opens frontmatter on line 1 and only when a closing delimiter
    exists; otherwise it is an ordinary horizontal rule and the text is returned
    untouched. Malformed YAML degrades to "no frontmatter" rather than failing
    the whole run.
    """
    if not text.startswith("---"):
        return None, "", text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "", text
    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            raw = "\n".join(lines[: idx + 1])
            body = "\n".join(lines[idx + 1 :])
            try:
                data = yaml.safe_load("\n".join(lines[1:idx]))
            except yaml.YAMLError:
                return None, "", text
            if data is None:
                data = {}
            if not isinstance(data, dict):
                return None, "", text
            return data, raw, body.lstrip("\n")
    return None, "", text


def parse_tree(body: str) -> DocNode:
    """Build a heading tree. Level 0 is the synthetic document root."""
    lines = body.splitlines()
    inside_fence = _fence_spans(lines)

    root = DocNode(level=0, heading=None, own_text="", line_start=1, line_end=len(lines))
    stack: list[DocNode] = [root]
    buffer: list[str] = []
    current = root

    def flush() -> None:
        current.own_text = "\n".join(buffer).strip("\n")

    for idx, line in enumerate(lines):
        if inside_fence[idx]:
            buffer.append(line)
            continue
        match = HEADING_RE.match(line)
        if not match:
            buffer.append(line)
            continue

        flush()
        current.line_end = idx
        buffer = []

        level = len(match.group(1))
        heading = (match.group(2) or "").strip()
        node = DocNode(
            level=level, heading=heading, own_text="", line_start=idx + 1, line_end=idx + 1
        )
        while len(stack) > 1 and stack[-1].level >= level:
            stack.pop()
        stack[-1].children.append(node)
        stack.append(node)
        current = node

    flush()
    current.line_end = len(lines)
    return root


def iter_nodes(node: DocNode):
    yield node
    for child in node.children:
        yield from iter_nodes(child)


def node_text(node: DocNode, include_heading: bool = False) -> str:
    """Full markdown of a subtree, heading levels preserved."""
    parts: list[str] = []
    if include_heading and node.heading is not None:
        parts.append(f"{'#' * node.level} {node.heading}")
    if node.own_text.strip():
        parts.append(node.own_text.strip("\n"))
    for child in node.children:
        parts.append(node_text(child, include_heading=True))
    return "\n\n".join(p for p in parts if p.strip())


def read_memdex_frontmatter(data: dict | None) -> dict | None:
    """The ``memdex:`` namespace of a generated file, validated."""
    if not isinstance(data, dict):
        return None
    block = data.get("memdex")
    if not isinstance(block, dict):
        return None
    unit_id = block.get("id")
    if not isinstance(unit_id, str) or not unit_id.startswith("mem_"):
        return None
    category = block.get("category")
    return {
        "id": unit_id,
        "title": str(block.get("title") or "").strip(),
        "category": category if category in CATEGORIES else "general",
        "description": str(block.get("description") or "").strip(),
        "importance": _safe_float(block.get("importance"), 0.5),
        "source": str(block.get("source") or "").strip(),
    }


def _safe_float(value, fallback: float) -> float:  # noqa: ANN001
    try:
        out = float(value)
    except (TypeError, ValueError):
        return fallback
    return min(0.95, max(0.05, out))


def strip_title_heading(body: str, title: str) -> str:
    """Drop a leading ``# Title`` line so it is not duplicated in the unit body."""
    lines = body.lstrip("\n").splitlines()
    if not lines:
        return ""
    match = HEADING_RE.match(lines[0])
    if match and (match.group(2) or "").strip().lower() == title.strip().lower():
        return "\n".join(lines[1:]).strip("\n")
    return body.strip("\n")


def title_for_file(path: Path, frontmatter: dict | None) -> str:
    if isinstance(frontmatter, dict):
        for key in ("title", "name"):
            value = frontmatter.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return humanize_stem(path.stem)


def is_generated_index(text: str, marker: str) -> bool:
    """True when the file carries the generated-index marker near its head."""
    return marker in text[:512]


def source_is_readonly(source: SourceFile) -> bool:
    return source.mode is SourceMode.READONLY
