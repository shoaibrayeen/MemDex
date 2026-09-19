"""Stage orchestration.

The pipeline is split in two halves on purpose. ``build_plan`` is pure: it reads
files and computes everything — the new tree, the index, the diff, the token
numbers — without touching disk or opening the vector store, which is exactly
what makes ``--dry-run`` trustworthy rather than a best-effort preview.
``apply_plan`` then performs the side effects in a fixed order: back up, write,
record identity, embed, index, log.

``run``, ``compact`` and ``index`` are the same stages with different masks, so
the three commands cannot drift apart.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from memdex import audit as audit_log
from memdex.backup import create_backup, prune_empty_dirs
from memdex.config import MemdexConfig
from memdex.discovery import count_source_roots, discover, ingestible
from memdex.embed import get_embedder
from memdex.embed.base import fingerprint, label_for
from memdex.errors import MemdexError
from memdex.identity import Registry, content_hash, dedupe_key
from memdex.indexdoc import render_index
from memdex.layout import diff_ops, plan_docs, unit_paths
from memdex.models import (
    MemoryPlan,
    MemoryUnit,
    PlanMode,
    RunReport,
    SourceFile,
    SourceMode,
)
from memdex.optimize.base import OptimizeContext, get_optimizer
from memdex.optimize.structure import tree_to_units
from memdex.parsing import (
    parse_tree,
    read_memdex_frontmatter,
    split_frontmatter,
    strip_title_heading,
    title_for_file,
)
from memdex.store import VectorRecord, VectorStore
from memdex.tokens import TokenCounter, get_counter
from memdex.util import atomic_write, plural

Progress = Callable[[str], None]
NOOP: Progress = lambda message: None  # noqa: E731


@dataclass
class ParsedSources:
    units: list[MemoryUnit]
    sources: list[SourceFile]
    warnings: list[str]


# ---------------------------------------------------------------------------
# P2-P5: discover, parse, unitize, measure
# ---------------------------------------------------------------------------
def parse_sources(cfg: MemdexConfig, counter: TokenCounter) -> ParsedSources:
    sources, warnings = discover(cfg)
    units: list[MemoryUnit] = []

    for source in ingestible(sources):
        if source.is_adopted_doc:
            units.append(_adopted_unit(source, counter))
            continue
        _, _, body = split_frontmatter(source.raw_text)
        if not body.strip():
            continue
        units.extend(tree_to_units(source, parse_tree(body), cfg.thresholds, counter))

    for unit in units:
        unit.token_count = counter.count(unit.body)
        unit.content_hash = content_hash(unit.body)
        unit.dedupe_key = dedupe_key(unit.body)

    return ParsedSources(units=units, sources=sources, warnings=warnings)


def _adopted_unit(source: SourceFile, counter: TokenCounter) -> MemoryUnit:
    """A file Memdex generated earlier: one unit, identity and labels preserved.

    Generated docs are never re-split or re-compressed. Their IDs may already be
    cited elsewhere, and re-deriving their metadata every run is what would make
    `memdex run` produce a different tree each time.
    """
    meta = read_memdex_frontmatter(source.frontmatter) or {}
    _, _, body = split_frontmatter(source.raw_text)
    title = meta.get("title") or title_for_file(Path(source.path), source.frontmatter)
    provenance = meta.get("source") or str(source.path)
    unit = MemoryUnit(
        title=title,
        body=strip_title_heading(body, title),
        source_file=source.path,
        source_mode=source.mode,
        id=meta.get("id"),
        breadcrumb=_breadcrumb_from_provenance(provenance, title),
        category=meta.get("category", "general"),
        description=meta.get("description", ""),
        importance=float(meta.get("importance", 0.5)),
        provenance=provenance,
        frontmatter_locked=True,
    )
    unit.token_count = counter.count(unit.body)
    return unit


def _breadcrumb_from_provenance(provenance: str, title: str) -> tuple[str, ...]:
    """Recover the heading path recorded as "file > Section > Title".

    Restoring it keeps what gets embedded identical between the run that created
    a memory and every run that re-reads it.
    """
    parts = [part.strip() for part in provenance.split(">")]
    if len(parts) < 3:
        return ()
    crumbs = parts[1:-1] if parts[-1] == title.strip() else parts[1:]
    return tuple(crumb for crumb in crumbs if crumb)


# ---------------------------------------------------------------------------
# P1-P14: build the plan
# ---------------------------------------------------------------------------
def build_plan(
    cfg: MemdexConfig,
    mode: PlanMode = PlanMode.RUN,
    use_llm: bool | None = None,
    progress: Progress = NOOP,
    embedder_label: str = "Local",
) -> MemoryPlan:
    counter = get_counter()
    report = RunReport(
        mode=mode, tokens_estimated=not counter.exact, embedder_label=embedder_label
    )
    warnings: list[str] = list(cfg.warnings)

    progress("Scanning project memory...")
    parsed = parse_sources(cfg, counter)
    warnings.extend(parsed.warnings)
    report.sources_found = count_source_roots(cfg, parsed.sources)
    report.files_scanned = len(parsed.sources)
    report.units_parsed = len(parsed.units)
    progress(f"Found {plural(report.sources_found, 'memory source')}")
    progress(f"Parsed {plural(report.units_parsed, 'memory section')}")

    report.tokens_before = sum(
        u.token_count for u in parsed.units if u.source_mode is SourceMode.MANAGED
    )

    registry = Registry.load(cfg.metadata_dir)
    warnings.extend(registry.warnings)
    registry.adopt(parsed.units)

    optimizer = get_optimizer(cfg, use_llm=use_llm)
    ctx = OptimizeContext(cfg=cfg, counter=counter, warn=warnings.append)
    units, analysis = optimizer.optimize(parsed.units, ctx)
    report.optimizer_label = optimizer.label
    report.exact_duplicates = len(analysis.exact_dupes)
    report.near_duplicates = len(analysis.near_dupes)
    progress(f"Detected {plural(report.exact_duplicates, 'duplicate')}")
    progress(f"Detected {plural(report.near_duplicates, 'redundant section')}")

    # New pieces created by an oversized split still need identities.
    registry.adopt([u for u in units if not u.id])
    live = [u for u in units if u.is_live]
    report.units_final = len(live)
    progress("Optimized memory structure")
    progress(f"Created {plural(report.units_final, 'memory unit')}")

    if mode is PlanMode.INDEX:
        docs = []
        paths = {u.id: u.source_file for u in live if u.id}
    else:
        docs = plan_docs(units, cfg)
        paths = unit_paths(docs, units)

    # Two different measurements, both honest, reported separately:
    #   context cost  — what an assistant loads unconditionally (all of the
    #                   memory before; only the index after), which is the point
    #                   of the product;
    #   content size  — the stored memory itself, which shrinks only by however
    #                   much duplication and filler there actually was.
    index_markdown = render_index(units, paths, cfg, counter)
    report.tokens_after = counter.count(index_markdown)
    report.content_before = report.tokens_before
    report.content_after = sum(
        u.token_count for u in live if u.source_mode is SourceMode.MANAGED
    )
    analysis.current_tokens = report.tokens_before
    analysis.optimized_tokens = report.tokens_after
    analysis.content_before = report.content_before
    analysis.content_after = report.content_after
    analysis.planned_files = [doc.path for doc in docs]
    progress("Generated MEMORY.md")

    ops = diff_ops(
        docs,
        index_markdown,
        parsed.sources,
        cfg,
        index_only=mode is PlanMode.INDEX,
    )

    report.files_written = sum(1 for op in ops if op.action in ("write", "overwrite"))
    report.files_deleted = sum(1 for op in ops if op.action == "delete")
    report.no_changes = not ops
    report.warnings = warnings

    live_ids = {u.id for u in live if u.id}
    embed_ids = _ids_needing_embedding(live, registry, paths)

    plan = MemoryPlan(
        mode=mode,
        units=units,
        docs=docs,
        index_path=cfg.rel_index_path,
        index_markdown=index_markdown,
        file_ops=ops,
        stale_ids=registry.stale_ids(live_ids),
        embed_ids=embed_ids,
        analysis=analysis,
        report=report,
        registry=registry,
        paths=paths,
    )
    return plan


def embedding_text(unit: MemoryUnit) -> str:
    """What actually gets embedded: the heading path as well as the body.

    A question like "how do I run the tests" matches the *title* "Testing" far
    more strongly than anything in its body, so leaving the title out of the
    vector loses the single best signal a memory has.
    """
    parts = [unit.title, " > ".join(unit.breadcrumb), unit.body]
    return "\n".join(part for part in parts if part.strip())


def _ids_needing_embedding(
    live: list[MemoryUnit], registry: Registry, paths: dict[str, Path]
) -> list[str]:
    """Units whose vector is missing, stale, or whose stored path moved."""
    needed: list[str] = []
    for unit in live:
        if not unit.id:
            continue
        entry = registry.entries.get(unit.id)
        if entry is None or entry.embedded_hash != unit.content_hash:
            needed.append(unit.id)
            continue
        current_path = paths.get(unit.id, unit.source_file)
        if entry.path != str(current_path):
            needed.append(unit.id)
    return sorted(needed)


def ensure_index_migrated(cfg: MemdexConfig) -> None:
    """`memdex index` must not overwrite memory that has not been migrated yet.

    Index mode only regenerates MEMORY.md; if that file still holds the user's
    original notes, rewriting it would push real content into a backup and call
    it an index.
    """
    from memdex.layout import index_has_marker

    index = cfg.abs_index_path
    if not index.is_file():
        return
    try:
        text = index.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    if index_has_marker(text) or not text.strip():
        return
    raise MemdexError(
        f"{cfg.index_path} still contains your own notes, not a generated index.",
        hint=(
            "Run `memdex run` first — it moves that content into memory/ "
            "and backs up the original."
        ),
    )


# ---------------------------------------------------------------------------
# A1-A7: apply
# ---------------------------------------------------------------------------
def apply_plan(
    cfg: MemdexConfig,
    plan: MemoryPlan,
    progress: Progress = NOOP,
    store: VectorStore | None = None,
    record_audit: bool = True,
) -> RunReport:
    report = plan.report
    registry: Registry = plan.registry
    paths: dict[str, Path] = plan.paths

    backup = create_backup(cfg, plan.file_ops, reason=plan.mode.value)
    report.backup_dir = backup.dir if backup else None

    _apply_file_ops(cfg, plan)

    live = [u for u in plan.units if u.is_live and u.id]
    registry.sync_entries(live, paths)

    store = store or VectorStore(cfg)
    embedder = get_embedder(cfg, warn=report.warnings.append)
    report.embedder_label = label_for(embedder)
    expected = fingerprint(embedder)

    rebuilt = store.ensure_fingerprint(expected, registry.embedder_fingerprint)
    if rebuilt:
        report.warnings.append(
            "The embedding model changed, so the search index was rebuilt from scratch."
        )
    registry.embedder_fingerprint = expected

    try:
        present = store.existing_ids()
    except MemdexError:
        present = set()

    to_embed = [u for u in live if rebuilt or u.id in set(plan.embed_ids) or u.id not in present]
    report.embeddings_skipped = len(live) - len(to_embed)

    records: list[VectorRecord] = []
    if to_embed:
        progress(f"Generated {plural(len(to_embed), 'local embedding')}")
        vectors = embedder.embed([embedding_text(u) for u in to_embed])
        for unit, vector in zip(to_embed, vectors, strict=True):
            records.append(
                VectorRecord(
                    id=unit.id or "",
                    text=unit.body,
                    embedding=vector,
                    metadata={
                        "memory_id": unit.id,
                        "title": unit.title,
                        "category": unit.category,
                        "source_file": paths.get(unit.id or "", unit.source_file).as_posix(),
                        "importance": float(unit.importance),
                        "token_count": int(unit.token_count),
                        "mode": unit.source_mode.value,
                    },
                )
            )
    report.embeddings_generated = len(records)

    stale = set(plan.stale_ids) | (present - {u.id for u in live})
    stats = store.sync(records, sorted(stale))
    report.warnings.extend(stats.warnings)
    registry.mark_embedded({r.id for r in records}, {u.id or "": u.content_hash for u in live})
    report.vector_count = store.count()
    progress(f"Indexed {plural(report.vector_count, 'memory', 'memories')} in ChromaDB")

    cfg.metadata_dir.mkdir(parents=True, exist_ok=True)
    registry.save(cfg.metadata_dir)
    _write_run_snapshot(cfg, plan)

    if record_audit:
        audit_log.record(cfg, store, audit_log.event_from_report(report, report.vector_count))
    return report


def _apply_file_ops(cfg: MemdexConfig, plan: MemoryPlan) -> None:
    rendered = {doc.path: doc.rendered for doc in plan.docs}
    for op in plan.file_ops:
        target = cfg.root / op.path
        if op.action == "delete":
            if target.is_file():
                target.unlink()
            continue
        if op.path == plan.index_path:
            atomic_write(target, plan.index_markdown)
        elif op.path in rendered:
            atomic_write(target, rendered[op.path])
    prune_empty_dirs(cfg.abs_output_dir)


def _write_run_snapshot(cfg: MemdexConfig, plan: MemoryPlan) -> None:
    report = plan.report
    cfg.index_dir.mkdir(parents=True, exist_ok=True)
    if plan.index_markdown:
        atomic_write(cfg.index_dir / "last_index.md", plan.index_markdown)
    snapshot = {
        "mode": report.mode.value,
        "tokens_before": report.tokens_before,
        "tokens_after": report.tokens_after,
        "tokens_saved": report.tokens_saved,
        "pct_saved": round(report.pct_saved, 2),
        "content_before": report.content_before,
        "content_after": report.content_after,
        "content_pct_saved": round(report.content_pct_saved, 2),
        "tokens_estimated": report.tokens_estimated,
        "units": report.units_final,
        "vectors": report.vector_count,
        "files_written": report.files_written,
        "files_deleted": report.files_deleted,
        "mode_label": report.mode_label,
        "categories": _category_counts(plan),
    }
    atomic_write(cfg.metadata_dir / "last_run.json", json.dumps(snapshot, indent=2) + "\n")
    atomic_write(cfg.index_dir / "stats.json", json.dumps(snapshot, indent=2) + "\n")


def _category_counts(plan: MemoryPlan) -> dict[str, int]:
    counts: dict[str, int] = {}
    for unit in plan.units:
        if unit.is_live:
            counts[unit.category] = counts.get(unit.category, 0) + 1
    return dict(sorted(counts.items()))


def read_last_run(cfg: MemdexConfig) -> dict | None:
    path = cfg.metadata_dir / "last_run.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
