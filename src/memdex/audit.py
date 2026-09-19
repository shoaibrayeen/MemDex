"""The activity log.

Every command that changes something appends one row, including `clean` — the
point being that wiping the memory database is itself a recorded event, so the
history never silently loses the fact that it was reset. The log lives in its
own Chroma collection and survives everything short of `memdex clean --all`.
"""

from __future__ import annotations

import uuid

from memdex.config import MemdexConfig
from memdex.models import AuditEvent, RunReport
from memdex.store import VectorStore
from memdex.util import fmt_int, fmt_pct, utc_now_iso


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:8]}"


def event_from_report(report: RunReport, vectors: int) -> AuditEvent:
    approx = "" if not report.tokens_estimated else "~"
    summary = (
        f"{report.mode.value}: {approx}{fmt_int(report.tokens_before)} → "
        f"{approx}{fmt_int(report.tokens_after)} tokens "
        f"({fmt_pct(report.pct_saved)} saved), {report.units_final} memories"
    )
    return AuditEvent(
        id=new_event_id(),
        event=report.mode.value,
        at=utc_now_iso(),
        summary=summary,
        tokens_before=report.tokens_before,
        tokens_after=report.tokens_after,
        tokens_saved=report.tokens_saved,
        pct_saved=report.pct_saved,
        files_written=report.files_written,
        units=report.units_final,
        vectors=vectors,
        mode_label=report.mode_label,
    )


def simple_event(event: str, summary: str, **fields) -> AuditEvent:
    return AuditEvent(id=new_event_id(), event=event, at=utc_now_iso(), summary=summary, **fields)


def record(cfg: MemdexConfig, store: VectorStore, event: AuditEvent) -> None:
    if not cfg.audit_enabled:
        return
    store.record_event(event)


def history(cfg: MemdexConfig, store: VectorStore, limit: int | None = None) -> list[AuditEvent]:
    if not cfg.audit_enabled:
        return []
    return store.read_events(limit=limit)
