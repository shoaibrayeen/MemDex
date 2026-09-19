"""The default optimizer: dedupe, categorize, compress, re-split, describe."""

from __future__ import annotations

from memdex.identity import content_hash, dedupe_key
from memdex.models import CompactAnalysis, MemoryUnit
from memdex.optimize.base import OptimizeContext
from memdex.optimize.categorize import categorize, is_obsolete, score_importance
from memdex.optimize.compress import compress, first_sentence
from memdex.optimize.dedupe import find_duplicates
from memdex.optimize.structure import split_oversized


class DeterministicOptimizer:
    label = "Deterministic"

    def optimize(
        self, units: list[MemoryUnit], ctx: OptimizeContext
    ) -> tuple[list[MemoryUnit], CompactAnalysis]:
        counter = ctx.counter
        thresholds = ctx.cfg.thresholds
        analysis = CompactAnalysis(tokens_estimated=not counter.exact)

        duplicates = find_duplicates(units, thresholds.duplicate_similarity)
        analysis.exact_dupes = duplicates.exact
        analysis.near_dupes = duplicates.near
        analysis.similar_pairs = duplicates.similar

        result: list[MemoryUnit] = []
        for unit in units:
            if not unit.is_live:
                result.append(unit)
                continue

            if not unit.frontmatter_locked:
                unit.body = compress(unit.body)
                if not unit.body.strip():
                    # Nothing but scaffolding — an unfilled template section, a
                    # comment-only block. It was never a memory; drop it.
                    continue
                unit.obsolete = is_obsolete(unit)
                unit.category = categorize(unit)
                unit.token_count = counter.count(unit.body)
                unit.importance = score_importance(unit)
            else:
                unit.obsolete = is_obsolete(unit)
                unit.token_count = counter.count(unit.body)

            pieces = split_oversized(unit, thresholds, counter)
            for piece in pieces:
                if piece is not unit:
                    piece.obsolete = is_obsolete(piece)
                    piece.category = categorize(piece)
                piece.token_count = counter.count(piece.body)
                if not piece.frontmatter_locked:
                    piece.importance = score_importance(piece)
                piece.content_hash = content_hash(piece.body)
                piece.dedupe_key = dedupe_key(piece.body)
                if not piece.description:
                    piece.description = first_sentence(piece.body)
                if piece.token_count > thresholds.max_file_tokens:
                    analysis.large_sections.append((piece.title, piece.token_count))
                if piece.obsolete:
                    analysis.obsolete.append(piece.title)
            result.extend(pieces)

        return result, analysis
