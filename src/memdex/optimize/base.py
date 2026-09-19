"""The optimizer abstraction.

Deterministic optimization is the product; the LLM is an optional enhancement
layer that runs *after* it and may only refine labels and confirm merges. That
ordering is what keeps Memdex usable offline and its output reproducible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from memdex.config import MemdexConfig
from memdex.models import CompactAnalysis, MemoryUnit
from memdex.tokens import TokenCounter

Warn = Callable[[str], None]


@dataclass
class OptimizeContext:
    cfg: MemdexConfig
    counter: TokenCounter
    warn: Warn = lambda message: None


class MemoryOptimizer(Protocol):
    label: str

    def optimize(
        self, units: list[MemoryUnit], ctx: OptimizeContext
    ) -> tuple[list[MemoryUnit], CompactAnalysis]: ...


def get_optimizer(cfg: MemdexConfig, use_llm: bool | None = None) -> MemoryOptimizer:
    """Pick an optimizer. ``use_llm`` overrides the configured mode when given."""
    from memdex.optimize.deterministic import DeterministicOptimizer

    wants_llm = cfg.optimizer_mode == "llm" if use_llm is None else use_llm
    deterministic = DeterministicOptimizer()
    if not wants_llm:
        return deterministic

    from memdex.llmclient import LLMClient
    from memdex.optimize.llm import LLMOptimizer

    return LLMOptimizer(inner=deterministic, client=LLMClient(cfg.llm), llm_cfg=cfg.llm)
