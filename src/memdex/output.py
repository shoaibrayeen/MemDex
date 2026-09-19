"""All terminal rendering.

Kept apart from the pipeline so the stages stay testable without a console, and
so the CLI reads as "compute, then show". Memdex is a CLI first: everything the
web dashboard displays has an equivalent here.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.theme import Theme

from memdex.models import AuditEvent, CompactAnalysis, MemoryPlan, RunReport, SearchHit
from memdex.util import fmt_int, fmt_pct, plural

THEME = Theme(
    {
        "ok": "green",
        "warn": "yellow",
        "err": "bold red",
        "dim": "dim",
        "head": "bold",
        "accent": "cyan",
    }
)

console = Console(theme=THEME, highlight=False)


def banner() -> None:
    console.print("\n[head]Memdex[/head]\n")


def step(message: str) -> None:
    console.print(f"[ok]✓[/ok] {message}")


def info(message: str) -> None:
    console.print(message)


def note(message: str) -> None:
    console.print(f"[dim]{message}[/dim]")


def warn(message: str) -> None:
    console.print(f"[warn]![/warn] {message}")


def error(message: str, hint: str = "") -> None:
    console.print(f"[err]✗[/err] {message}")
    if hint:
        for line in hint.splitlines():
            console.print(f"  [dim]{line}[/dim]")


def _approx(estimated: bool) -> str:
    return "~" if estimated else ""


def token_block(report: RunReport, title: str = "Token Optimization") -> None:
    approx = _approx(report.tokens_estimated)
    console.print(f"\n[head]{title}[/head]\n")
    note("  Context loaded by an assistant (whole memory → index only)")
    console.print(f"  Before: {approx}{fmt_int(report.tokens_before):>10}  tokens")
    console.print(f"  After:  {approx}{fmt_int(report.tokens_after):>10}  tokens")
    console.print(f"  Saved:  {approx}{fmt_int(report.tokens_saved):>10}  tokens")
    console.print(f"          [accent]{fmt_pct(report.pct_saved):>11}[/accent]")
    if report.content_before:
        console.print(
            f"\n  [dim]Memory content kept on disk:[/dim] {approx}{fmt_int(report.content_before)}"
            f" → {approx}{fmt_int(report.content_after)} tokens"
            f" ([accent]{fmt_pct(report.content_pct_saved)}[/accent] smaller, retrieved on demand)"
        )
    if report.tokens_estimated:
        note("  (estimated — install the `tokens` extra for exact counts)")


def run_summary(report: RunReport, cfg) -> None:
    console.print("\n  Memory index:")
    console.print(f"    {cfg.index_path}")
    console.print("\n  Storage:")
    console.print(f"    {cfg.store_path}  ({report.vector_count} vectors)")
    console.print("\n  Mode:")
    console.print(f"    {report.mode_label}")
    for message in report.warnings:
        warn(message)
    console.print("\n[ok]✓ Complete[/ok]\n")


def dry_run_changes(plan: MemoryPlan, cfg) -> None:
    console.print("\n[head]Proposed changes[/head]\n")
    if not plan.file_ops:
        console.print("  Nothing to change — memory is already optimized.")
        return
    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    table.add_column("Action")
    table.add_column("File")
    table.add_column("Why", style="dim")
    for op in plan.file_ops:
        colour = {"write": "ok", "overwrite": "accent", "delete": "warn"}[op.action]
        table.add_row(f"[{colour}]{op.action}[/{colour}]", str(op.path), op.reason)
    console.print(table)


def compact_analysis(analysis: CompactAnalysis, plan: MemoryPlan, dry_run: bool) -> None:
    approx = _approx(analysis.tokens_estimated)
    console.print("\n[head]Memory Analysis[/head]\n")
    console.print(f"  Context now loaded:  {approx}{fmt_int(analysis.current_tokens)} tokens")
    console.print(f"  After optimization:  {approx}{fmt_int(analysis.optimized_tokens)} tokens")
    console.print(f"  Reduction:           [accent]{fmt_pct(analysis.pct_saved)}[/accent]")
    if analysis.content_before:
        console.print(
            f"  Memory content:      {approx}{fmt_int(analysis.content_before)} → "
            f"{approx}{fmt_int(analysis.content_after)} tokens "
            f"({fmt_pct(analysis.content_pct_saved)} smaller)"
        )

    def section(label: str, count: int, details: list[str], suffix: str = "detected") -> None:
        console.print(f"\n  {label}:\n    {count} {suffix}")
        for detail in details[:5]:
            note(f"      · {detail}")
        if len(details) > 5:
            note(f"      … and {len(details) - 5} more")

    section("Duplicates", len(analysis.exact_dupes), [w for _, w in analysis.exact_dupes])
    section("Redundant sections", len(analysis.near_dupes), [w for _, w, _ in analysis.near_dupes])
    section(
        "Large sections",
        len(analysis.large_sections),
        [f"{title} ({fmt_int(tokens)} tokens)" for title, tokens in analysis.large_sections],
    )
    section("Potentially obsolete", len(analysis.obsolete), list(analysis.obsolete))
    section(
        "Similar, kept apart",
        len(analysis.similar_pairs),
        [f"{a} ≈ {b} ({ratio:.2f})" for a, b, ratio in analysis.similar_pairs],
        suffix="pair(s)",
    )

    console.print(
        f"\n  Would create:\n    {plural(len(analysis.planned_files), 'memory file')}"
        "\n    1 memory index"
    )
    if dry_run:
        console.print("\n[dim]Run without --dry-run to apply.[/dim]\n")


def search_results(hits: list[SearchHit], query: str) -> None:
    if not hits:
        console.print(f"\nNo memories matched [accent]{query}[/accent].\n")
        return
    console.print(f"\n{plural(len(hits), 'memory', 'memories')} found\n")
    for rank, hit in enumerate(hits, start=1):
        console.print(f"{rank}. [head]{hit.title}[/head]")
        console.print(f"   {hit.path}")
        console.print(f"   [dim]Score: {hit.score:.2f} · {hit.category}[/dim]")
        if hit.snippet:
            console.print(f"   [dim]{hit.snippet}[/dim]")
        console.print()


def status_table(cfg, last_run: dict | None, counts: dict, backups: int) -> None:
    console.print("\n[head]Memdex status[/head]\n")
    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Project", str(cfg.root))
    table.add_row("Index", cfg.index_path)
    table.add_row("Memory directory", cfg.output_dir)
    table.add_row("Memory files", str(counts.get("files", 0)))
    table.add_row("Indexed memories", str(counts.get("units", 0)))
    table.add_row("Vectors", str(counts.get("vectors", 0)))
    table.add_row("Embedding", f"{cfg.embedding_provider} ({cfg.embedding_model})")
    table.add_row("Optimizer", cfg.optimizer_mode)
    table.add_row("LLM", cfg.llm.label if cfg.llm.is_usable else "disabled")
    table.add_row("Backups", str(backups))
    if last_run:
        approx = "~" if last_run.get("tokens_estimated", True) else ""
        table.add_row(
            "Last run",
            f"{last_run.get('mode', '?')} · {approx}{fmt_int(last_run.get('tokens_before', 0))} → "
            f"{approx}{fmt_int(last_run.get('tokens_after', 0))} tokens "
            f"({last_run.get('pct_saved', 0)}% saved)",
        )
    else:
        table.add_row("Last run", "never — run `memdex run`")
    console.print(table)
    console.print()


SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return SPARK[len(SPARK) // 2] * len(values)
    return "".join(SPARK[int((v - lo) / (hi - lo) * (len(SPARK) - 1))] for v in values)


def history_table(events: list[AuditEvent]) -> None:
    if not events:
        console.print("\nNo activity recorded yet. Run `memdex run` to get started.\n")
        return
    console.print("\n[head]Memdex history[/head]\n")
    table = Table(box=None, pad_edge=False, header_style="dim")
    table.add_column("When")
    table.add_column("Event")
    table.add_column("Before", justify="right")
    table.add_column("After", justify="right")
    table.add_column("Saved", justify="right")
    table.add_column("Memories", justify="right")
    for event in events:
        saved = f"{event.pct_saved:.1f}%" if event.tokens_before else "—"
        table.add_row(
            event.at.replace("T", " ").replace("Z", ""),
            event.event,
            fmt_int(event.tokens_before) if event.tokens_before else "—",
            fmt_int(event.tokens_after) if event.tokens_before else "—",
            saved,
            str(event.units) if event.units else "—",
        )
    console.print(table)

    optimizations = [e for e in events if e.tokens_before]
    if len(optimizations) > 1:
        console.print(
            f"\n  Reduction over time  "
            f"[accent]{sparkline([e.pct_saved for e in optimizations])}[/accent]"
            f"  (latest {optimizations[-1].pct_saved:.1f}%)"
        )
    cleans = [e for e in events if e.event == "clean"]
    if cleans:
        note(
            f"\n  {plural(len(cleans), 'clean event')} recorded — "
            "memory data was reset, log kept."
        )
    console.print()


def doctor_table(rows: list[tuple[str, bool, str]]) -> None:
    console.print("\n[head]Memdex doctor[/head]\n")
    for name, ok, detail in rows:
        mark = "[ok]✓[/ok]" if ok else "[err]✗[/err]"
        console.print(f"{mark} {name}")
        if detail:
            note(f"    {detail}")
    console.print()


def backups_table(backups) -> None:
    table = Table(box=None, pad_edge=False, header_style="dim")
    table.add_column("Timestamp")
    table.add_column("Reason")
    table.add_column("Files", justify="right")
    for info in backups:
        table.add_row(info.stamp, info.reason, str(len(info.backed_up) + len(info.created)))
    console.print(table)


def init_summary(cfg, sources: list[Path], mode_line: str) -> None:
    console.print("\n[ok]✓ Memdex initialized[/ok]\n")
    console.print("Memory sources:")
    if sources:
        for path in sources:
            console.print(f"  {path}")
    else:
        console.print("  [dim]none found yet[/dim]")
    console.print("\nStorage:")
    console.print(f"  Local ChromaDB ({cfg.store_path})")
    console.print("\nMode:")
    console.print(f"  {mode_line}")
    console.print("\nRun:")
    console.print("  memdex run\n")
