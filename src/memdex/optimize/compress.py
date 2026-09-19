"""Conservative compression.

The rule that governs this module: **never change what the memory means**. So
there is no paraphrasing, no reflowing and no touching the inside of a code
fence — only removal of text that carries no information (assistant chatter,
blank-line drifts, a paragraph repeated verbatim). Everything here is a fixed
point: compressing twice gives the same result as compressing once, which is
what lets a second `memdex run` be a genuine no-op.
"""

from __future__ import annotations

import re

from memdex.parsing import _fence_spans

FILLER_PATTERNS = (
    r"(sure|okay|ok|great|certainly|of course|absolutely|no problem)[!,.]?\s*(here|let|i)?.*",
    r"here('s| is) (what|the|a) (i|you|we)?.*",
    r"(let me|i'll|i will|i can|i've|i have) (know|explain|help|summarize|walk|go|add|update).*",
    r"as an ai( language model)?.*",
    r"(hope (this|that) helps|feel free to ask|let me know if.*)",
    r"(in summary|to summarize|in conclusion)[:,]?\s*",
    r"(good|great) (question|point)[!.]?",
)
FILLER_RE = re.compile(rf"^\s*(?:{'|'.join(FILLER_PATTERNS)})[!.…\s]*$", re.IGNORECASE)
MAX_FILLER_LINE = 120

# Template placeholders ("_(none yet)_", with or without a list dash) mark a
# section the assistant has not filled yet — they are scaffolding, not memory.
PLACEHOLDER_RE = re.compile(r"^\s*[-*+]?\s*_\(none yet\)_\s*$")

SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
# Only paired markers are emphasis. A bare underscore belongs to an identifier
# such as tenant_id, and stripping it corrupts the very detail worth recording.
EMPHASIS_RE = re.compile(r"(\*\*|\*|__|`)(.+?)\1")
BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
HEADING_PREFIX_RE = re.compile(r"^ {0,3}#{1,6} +")


def _is_filler(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > MAX_FILLER_LINE:
        return False
    if stripped.startswith(("#", ">", "-", "*", "|", "+")) or stripped[0].isdigit():
        return False
    return bool(FILLER_RE.match(stripped))


def _strip_html_comments(lines: list[str], inside: list[bool]) -> tuple[list[str], list[bool]]:
    """Remove <!-- … --> outside code fences, including multi-line blocks.

    Comments are authorial scaffolding — template instructions, section hints —
    not memory. Inside a fence they are code and stay byte-identical.
    """
    out_lines: list[str] = []
    out_inside: list[bool] = []
    in_comment = False
    for line, fenced in zip(lines, inside, strict=True):
        if fenced:
            out_lines.append(line)
            out_inside.append(True)
            continue
        text = line
        while True:
            if in_comment:
                close = text.find("-->")
                if close == -1:
                    text = None
                    break
                text = text[close + 3 :]
                in_comment = False
            else:
                start = text.find("<!--")
                if start == -1:
                    break
                close = text.find("-->", start + 4)
                if close == -1:
                    text = text[:start]
                    in_comment = True
                    break
                text = text[:start] + text[close + 3 :]
        if text is None:
            continue
        if line.strip() and not text.strip():
            continue  # the line was only a comment; do not leave a blank behind
        out_lines.append(text)
        out_inside.append(False)
    return out_lines, out_inside


def compress(body: str) -> str:
    """Drop scaffolding and filler, collapse blank runs, remove repeated paragraphs."""
    lines = body.splitlines()
    inside = _fence_spans(lines)
    lines, inside = _strip_html_comments(lines, inside)

    kept: list[str] = []
    kept_inside: list[bool] = []
    for idx, line in enumerate(lines):
        if not inside[idx] and (_is_filler(line) or PLACEHOLDER_RE.match(line)):
            continue
        kept.append(line.rstrip() if not inside[idx] else line)
        kept_inside.append(inside[idx])

    # Collapse runs of blank lines outside fences.
    collapsed: list[str] = []
    collapsed_inside: list[bool] = []
    blanks = 0
    for line, in_fence in zip(kept, kept_inside, strict=True):
        if not in_fence and not line.strip():
            blanks += 1
            if blanks > 1:
                continue
        else:
            blanks = 0
        collapsed.append(line)
        collapsed_inside.append(in_fence)

    return _drop_repeated_paragraphs(collapsed, collapsed_inside).strip("\n")


def _drop_repeated_paragraphs(lines: list[str], inside: list[bool]) -> str:
    """Remove a paragraph that repeats one already seen, verbatim."""
    paragraphs: list[tuple[str, bool]] = []
    buffer: list[str] = []
    buffer_fenced = False

    def flush() -> None:
        nonlocal buffer, buffer_fenced
        if buffer:
            paragraphs.append(("\n".join(buffer), buffer_fenced))
        buffer = []
        buffer_fenced = False

    for line, in_fence in zip(lines, inside, strict=True):
        if in_fence:
            buffer_fenced = True
        if not in_fence and not line.strip():
            flush()
            continue
        buffer.append(line)
    flush()

    seen: set[str] = set()
    out: list[str] = []
    for text, fenced in paragraphs:
        key = re.sub(r"\s+", " ", text).strip().casefold()
        # Headings and code keep their duplicates: the same fence can legitimately
        # appear under two different headings, and dropping one would corrupt it.
        if fenced or HEADING_PREFIX_RE.match(text) or len(key) < 40:
            out.append(text)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return "\n\n".join(out)


def first_sentence(body: str, max_length: int = 120) -> str:
    """One-line description for the index: the first real sentence of the memory.

    Reads a whole paragraph before splitting, because memory files are usually
    hard-wrapped and taking the first *line* would cut mid-clause.
    """
    lines = body.splitlines()
    inside = _fence_spans(lines)

    paragraph: list[str] = []
    for idx, line in enumerate(lines):
        if inside[idx]:
            if paragraph:
                break
            continue
        stripped = line.strip()
        if not stripped or HEADING_PREFIX_RE.match(line):
            if paragraph:
                break
            continue
        stripped = BULLET_RE.sub("", stripped)
        stripped = re.sub(r"^>\s*", "", stripped).strip()
        if not stripped:
            continue
        paragraph.append(stripped)
        if SENTENCE_END_RE.search(stripped + " "):
            break

    if not paragraph:
        # A memory that is nothing but commands still deserves a description;
        # its first command says more than an empty index line does.
        return _first_code_line(lines, inside, max_length)

    text = " ".join(paragraph)
    sentence = SENTENCE_END_RE.split(text, maxsplit=1)[0].strip()
    sentence = LINK_RE.sub(r"\1", sentence)
    sentence = EMPHASIS_RE.sub(r"\2", sentence).strip()
    if len(sentence) > max_length:
        clipped = sentence[:max_length].rsplit(" ", 1)[0].rstrip(",;:")
        sentence = f"{clipped}…"
    return sentence


def _first_code_line(lines: list[str], inside: list[bool], max_length: int) -> str:
    for idx, line in enumerate(lines):
        if not inside[idx]:
            continue
        stripped = line.strip()
        # Skip the fence delimiters themselves and comment-only lines.
        if not stripped or stripped.startswith(("```", "~~~", "#", "//")):
            continue
        if len(stripped) > max_length:
            stripped = stripped[:max_length].rstrip() + "…"
        return f"`{stripped}`"
    return ""
