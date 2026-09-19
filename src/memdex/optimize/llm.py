"""The optional LLM layer.

It runs *after* the deterministic optimizer and is allowed to do only two
things: improve labels (title, description, category, importance, obsolete), and
confirm or veto merges the deterministic pass already proposed. It never
rewrites a memory's body and never invents a merge of its own — a model that
hallucinates in this position would silently rewrite the user's knowledge, and
the whole product promise is that it does not.

Any failure at all (unreachable, slow, malformed, out of contract) is a warning,
not an error: the deterministic result stands and the run succeeds.
"""

from __future__ import annotations

from memdex.config import LLMConfig
from memdex.errors import LLMError
from memdex.models import CATEGORIES, CompactAnalysis, MemoryUnit
from memdex.optimize.base import OptimizeContext
from memdex.optimize.deterministic import DeterministicOptimizer

BATCH_SIZE = 10
MAX_TITLE = 80
MAX_DESCRIPTION = 140

REFINE_SYSTEM = (
    "You label developer memory notes for an index. "
    "Reply with JSON only: {\"units\":[{\"id\":str,\"title\":str,\"description\":str,"
    "\"category\":str,\"importance\":number,\"obsolete\":boolean}]}. "
    f"category must be one of: {', '.join(CATEGORIES)}. "
    "title: at most 80 characters, no trailing punctuation. "
    "description: one sentence, at most 140 characters, describing what the note covers. "
    "importance: 0 to 1. Never invent facts, never change technical meaning, "
    "and only include ids that were given to you."
)

MERGE_SYSTEM = (
    "You review proposed duplicate merges between developer memory notes. "
    "Reply with JSON only: {\"merges\":[{\"id\":str,\"merge\":boolean}]}. "
    "merge=true only when the two notes say the same thing and nothing would be lost. "
    "Only answer about ids that were given to you."
)


class LLMOptimizer:
    def __init__(self, inner: DeterministicOptimizer, client, llm_cfg: LLMConfig) -> None:  # noqa: ANN001
        self.inner = inner
        self.client = client
        self.llm_cfg = llm_cfg

    @property
    def label(self) -> str:
        return f"LLM ({self.llm_cfg.provider}:{self.llm_cfg.model})"

    def optimize(
        self, units: list[MemoryUnit], ctx: OptimizeContext
    ) -> tuple[list[MemoryUnit], CompactAnalysis]:
        units, analysis = self.inner.optimize(units, ctx)
        candidates = [u for u in units if u.is_live and u.is_managed and u.id]
        if not candidates:
            return units, analysis

        failures = 0
        for start in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[start : start + BATCH_SIZE]
            try:
                self._refine_batch(batch)
            except LLMError as exc:
                failures += 1
                if failures == 1:
                    ctx.warn(f"{exc.message} Keeping the deterministic result.")

        if failures:
            ctx.warn(
                f"The LLM layer failed on {failures} batch(es); "
                "those memories kept their deterministic labels."
            )
        try:
            self._review_merges(units, analysis, ctx)
        except LLMError:
            pass  # already warned above if the endpoint is down
        return units, analysis

    # -- passes ------------------------------------------------------------
    def _refine_batch(self, batch: list[MemoryUnit]) -> None:
        payload = {
            "units": [
                {
                    "id": unit.id,
                    "current_title": unit.title,
                    "breadcrumb": " > ".join(unit.breadcrumb),
                    "content": unit.body[:4000],
                }
                for unit in batch
            ]
        }
        import json

        data = self.client.chat_json(REFINE_SYSTEM, json.dumps(payload))
        by_id = {unit.id: unit for unit in batch}
        for item in data.get("units") or []:
            if not isinstance(item, dict):
                continue
            unit = by_id.get(item.get("id"))
            if unit is None:  # a hallucinated id refers to nothing we sent
                continue
            self._apply_labels(unit, item)

    def _apply_labels(self, unit: MemoryUnit, item: dict) -> None:
        """Each field is taken only if it is valid; otherwise the old value stands."""
        title = item.get("title")
        if isinstance(title, str) and 0 < len(title.strip()) <= MAX_TITLE:
            unit.title = title.strip()

        description = item.get("description")
        if isinstance(description, str) and 0 < len(description.strip()) <= MAX_DESCRIPTION:
            unit.description = description.strip()

        category = item.get("category")
        if isinstance(category, str) and category in CATEGORIES:
            unit.category = category

        importance = item.get("importance")
        if isinstance(importance, (int, float)) and 0.0 <= float(importance) <= 1.0:
            unit.importance = round(min(0.95, max(0.05, float(importance))), 2)

        obsolete = item.get("obsolete")
        if isinstance(obsolete, bool):
            unit.obsolete = obsolete

    def _review_merges(
        self, units: list[MemoryUnit], analysis: CompactAnalysis, ctx: OptimizeContext
    ) -> None:
        """Let the model veto near-duplicate merges the deterministic pass made."""
        if not analysis.near_dupes:
            return
        shadowed = {u.provenance: u for u in units if not u.is_live}
        pairs = [
            (provenance, winner, ratio)
            for provenance, winner, ratio in analysis.near_dupes
            if provenance in shadowed
        ]
        if not pairs:
            return

        import json

        payload = {
            "pairs": [
                {
                    "id": provenance,
                    "kept_title": winner,
                    "similarity": ratio,
                    "content": shadowed[provenance].body[:2000],
                }
                for provenance, winner, ratio in pairs[:BATCH_SIZE]
            ]
        }
        data = self.client.chat_json(MERGE_SYSTEM, json.dumps(payload))
        vetoed = {
            item.get("id")
            for item in (data.get("merges") or [])
            if isinstance(item, dict) and item.get("merge") is False
        }
        if not vetoed:
            return
        for provenance in vetoed:
            unit = shadowed.get(provenance)
            if unit is not None:
                unit.shadowed_by = None
        analysis.near_dupes = [p for p in analysis.near_dupes if p[0] not in vetoed]
        ctx.warn(f"The LLM kept {len(vetoed)} memory(ies) the duplicate check wanted to merge.")
