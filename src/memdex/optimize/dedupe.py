"""Duplicate detection.

Two passes: an exact match on the aggressively normalized text (catches the same
note pasted into two files), then a similarity pass for the near-misses that
accumulate when a memory gets restated in slightly different words. The second
pass stays cheap because a word-overlap prefilter rejects most pairs before the
expensive sequence comparison runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from memdex.identity import normalize_aggressive
from memdex.models import MemoryUnit, SourceMode

WORD_RE = re.compile(r"[a-z0-9]+")
PREFILTER_JACCARD = 0.5


def _words(text: str) -> set[str]:
    return set(WORD_RE.findall(text))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    if not intersection:
        return 0.0
    return intersection / len(a | b)


def _precedence(unit: MemoryUnit) -> tuple:
    """Lower sorts first and therefore wins a duplicate pair.

    Managed memory outranks readonly memory: a managed copy is the one Memdex
    can curate and keep current, and shadowing it in favour of a file it may
    never touch would strand the canonical version.
    """
    return (
        0 if unit.source_mode is SourceMode.MANAGED else 1,
        0 if unit.id else 1,
        -unit.token_count,
        str(unit.source_file),
        unit.line_start,
    )


@dataclass
class DedupeResult:
    exact: list[tuple[str, str]] = field(default_factory=list)
    near: list[tuple[str, str, float]] = field(default_factory=list)
    similar: list[tuple[str, str, float]] = field(default_factory=list)

    @property
    def exact_count(self) -> int:
        return len(self.exact)

    @property
    def near_count(self) -> int:
        return len(self.near)


def find_duplicates(units: list[MemoryUnit], threshold: float) -> DedupeResult:
    """Mark duplicate units as shadowed and report what was found.

    Shadowed units keep their content on disk (readonly sources are untouched,
    managed originals live on in the backup) — they are simply left out of the
    index, the output tree and the vector store.
    """
    result = DedupeResult()
    ordered = sorted(units, key=_precedence)

    # -- exact duplicates ---------------------------------------------------
    by_key: dict[str, MemoryUnit] = {}
    for unit in ordered:
        if not unit.dedupe_key:
            continue
        winner = by_key.get(unit.dedupe_key)
        if winner is None:
            by_key[unit.dedupe_key] = unit
            continue
        unit.shadowed_by = winner.id or winner.title
        winner.merged_from.append(unit.provenance)
        result.exact.append((unit.provenance, winner.title))

    # -- near duplicates ----------------------------------------------------
    survivors = [u for u in ordered if u.is_live]
    normalized = {id(u): normalize_aggressive(u.body) for u in survivors}
    word_sets = {id(u): _words(normalized[id(u)]) for u in survivors}

    for i, unit in enumerate(survivors):
        if not unit.is_live:
            continue
        for other in survivors[i + 1 :]:
            if not other.is_live:
                continue
            overlap = _jaccard(word_sets[id(unit)], word_sets[id(other)])
            if overlap < PREFILTER_JACCARD:
                continue
            ratio = SequenceMatcher(None, normalized[id(unit)], normalized[id(other)]).ratio()
            if ratio >= threshold:
                other.shadowed_by = unit.id or unit.title
                unit.merged_from.append(other.provenance)
                result.near.append((other.provenance, unit.title, round(ratio, 3)))
            else:
                result.similar.append((other.title, unit.title, round(ratio, 3)))

    return result
