"""The Memdex command line.

Every command is thin: resolve the project, run a pipeline or a service, hand
the result to ``output``. Memdex is a CLI tool first — the web dashboard is an
optional view over the same data, never a requirement.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import typer

from memdex import __version__, output
from memdex import audit as audit_log
from memdex.config import MEMDEX_DIR, MemdexConfig, default_config_yaml, load_config
from memdex.errors import ConfigError, MemdexError
from memdex.lock import acquire
from memdex.models import PlanMode
from memdex.project import ensure_code_project, find_base_dir, resolve_root
from memdex.util import atomic_write

app = typer.Typer(
    name="memdex",
    help="Local-first memory optimizer for AI coding assistants.",
    no_args_is_help=True,
    add_completion=False,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _fail(exc: MemdexError) -> None:
    output.error(exc.message, exc.hint)
    raise typer.Exit(code=1)


def _load() -> MemdexConfig:
    try:
        return load_config(resolve_root())
    except MemdexError as exc:
        _fail(exc)
        raise  # unreachable, keeps type checkers happy


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"memdex {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True,
        help="Show the Memdex version and exit.",
    ),
) -> None:
    """Turn sprawling AI memory into a small index plus a local semantic search."""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------
@app.command()
def init(
    force: bool = typer.Option(False, "--force", help="Initialize even outside a code project."),
    base: bool = typer.Option(False, "--base", help="Use the repository root without asking."),
    here: bool = typer.Option(False, "--here", help="Use the current directory without asking."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept defaults; never prompt."),
    no_ui: bool = typer.Option(
        False, "--no-ui", help="Write a config with the dashboard disabled."
    ),
    offline: bool = typer.Option(
        False, "--offline", help="Use the offline hash embedder (no model download)."
    ),
) -> None:
    """Set up Memdex in this project."""
    cwd = Path.cwd().resolve()
    try:
        ensure_code_project(cwd, force=force)
    except MemdexError as exc:
        _fail(exc)

    target = _resolve_init_target(cwd, base=base, here=here, yes=yes)
    memdex_dir = target / MEMDEX_DIR
    config_file = memdex_dir / "config.yaml"

    if config_file.exists() and not force:
        output.error(
            f"Memdex is already initialized in {target}.",
            hint="Use --force to rewrite .memdex/config.yaml (your memory files are untouched).",
        )
        raise typer.Exit(code=1)

    for sub in ("chroma", "index", "metadata", "backups"):
        (memdex_dir / sub).mkdir(parents=True, exist_ok=True)
    atomic_write(
        config_file,
        default_config_yaml(
            ui_enabled=not no_ui,
            embedding_provider="hash" if offline else "local",
        ),
    )
    # Keep the vector store and backups out of version control, but let the
    # config travel with the repo if the user wants it to.
    atomic_write(memdex_dir / ".gitignore", "*\n!config.yaml\n!.gitignore\n")

    cfg = load_config(target)
    from memdex.discovery import discover

    sources, _ = discover(cfg)
    found = sorted({s.path.parts[0] if len(s.path.parts) > 1 else str(s.path) for s in sources})
    mode = "Deterministic / Local" if not offline else "Deterministic / Offline (hash)"
    output.init_summary(cfg, [Path(p) for p in found], mode)


def _resolve_init_target(cwd: Path, base: bool, here: bool, yes: bool) -> Path:
    """Decide whether a monorepo user means the repo root or this subdirectory."""
    if here:
        return cwd
    base_dir = find_base_dir(cwd)
    if base_dir is None or base_dir == cwd:
        return cwd
    if base or yes:
        return base_dir
    if not _interactive():
        output.note(
            f"Not a terminal — initializing the repository root ({base_dir}). "
            "Pass --here to use this directory instead."
        )
        return base_dir

    output.info(f"\nYou're inside a larger repository: [accent]{base_dir}[/accent]")
    output.info("Initialize Memdex for:")
    output.info(f"  [head]1[/head]  the repository root  ({base_dir})")
    output.info(f"  [head]2[/head]  this directory       ({cwd})")
    choice = typer.prompt("Choose", default="1").strip()
    return cwd if choice == "2" else base_dir


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change, write nothing."),
    llm: bool | None = typer.Option(
        None, "--llm/--no-llm", help="Use the optional LLM layer for this run."
    ),
) -> None:
    """Discover, optimize, reorganize and index this project's memory."""
    cfg = _load()
    _execute(cfg, PlanMode.RUN, dry_run=dry_run, use_llm=llm)


@app.command()
def compact(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report only; change nothing."),
    llm: bool | None = typer.Option(None, "--llm/--no-llm", help="Use the optional LLM layer."),
) -> None:
    """Squeeze duplicates and redundancy out of memory that is already organized."""
    cfg = _load()
    _execute(cfg, PlanMode.COMPACT, dry_run=dry_run, use_llm=llm)


@app.command(name="index")
def index_cmd() -> None:
    """Regenerate MEMORY.md and refresh the search index, without restructuring."""
    from memdex.pipeline import ensure_index_migrated

    cfg = _load()
    try:
        ensure_index_migrated(cfg)
    except MemdexError as exc:
        _fail(exc)
    _execute(cfg, PlanMode.INDEX, dry_run=False, use_llm=False)


def _execute(cfg: MemdexConfig, mode: PlanMode, dry_run: bool, use_llm: bool | None) -> None:
    from memdex.pipeline import apply_plan, build_plan

    if use_llm and not cfg.llm.is_usable:
        _fail(
            ConfigError(
                "The LLM layer is not configured.",
                hint=(
                    "Set llm.enabled: true and llm.model in .memdex/config.yaml.\n"
                    "For Ollama: `ollama pull qwen2.5:14b`, then model: qwen2.5:14b."
                ),
            )
        )

    if mode is PlanMode.RUN and not dry_run and _no_memory_anywhere(cfg):
        if _offer_bootstrap(cfg):
            return

    llm_active = use_llm if use_llm is not None else cfg.optimizer_mode == "llm"
    if llm_active:
        _warn_if_remote_llm(cfg)

    output.banner()
    try:
        with acquire(cfg.memdex_dir):
            plan = build_plan(cfg, mode=mode, use_llm=use_llm, progress=output.step)
            plan.report.dry_run = dry_run

            if mode is PlanMode.COMPACT:
                output.compact_analysis(plan.analysis, plan, dry_run)
            if dry_run:
                output.dry_run_changes(plan, cfg)
                output.token_block(plan.report, title="Token Optimization (projected)")
                for message in plan.report.warnings:
                    output.warn(message)
                output.note("\nNothing was written. Run without --dry-run to apply.\n")
                return

            report = apply_plan(cfg, plan, progress=output.step)
    except MemdexError as exc:
        _fail(exc)
        return

    if report.no_changes:
        output.note("\nMemory is already optimized — nothing changed.")
    if mode is not PlanMode.COMPACT:
        output.token_block(report)
        output.info(f"\n  Memory files:\n    {report.units_final}")
    output.run_summary(report, cfg)


def _warn_if_remote_llm(cfg: MemdexConfig) -> None:
    """Leaving the machine is the user's call, but it should never be a surprise."""
    if cfg.llm.is_usable and cfg.llm.is_remote:
        output.warn(
            f"The LLM endpoint is remote ({cfg.llm.resolved_base_url}) — "
            "memory content will be sent to it."
        )


def _no_memory_anywhere(cfg: MemdexConfig) -> bool:
    from memdex.discovery import discover, ingestible

    sources, _ = discover(cfg)
    return not ingestible(sources)


def _offer_bootstrap(cfg: MemdexConfig) -> bool:
    """No memory to optimize yet. Offer to generate some from the codebase."""
    output.warn("No project memory found in the configured sources.")
    if not cfg.llm.is_usable:
        output.info(
            "\nMemdex can write a first MEMORY.md by reading your codebase, but that needs an\n"
            "LLM with a large context window. Configure one in .memdex/config.yaml:\n"
        )
        output.note("  llm:\n    enabled: true\n    provider: ollama\n    model: qwen2.5:14b")
        output.info("\nThen run: [accent]memdex bootstrap[/accent]\n")
        return True
    if not _interactive():
        output.info("\nRun [accent]memdex bootstrap[/accent] to generate memory from your code.\n")
        return True
    if typer.confirm("\nGenerate one from your codebase with the configured LLM?", default=False):
        _bootstrap(cfg, dry_run=False)
        return True
    output.note("Nothing to do. Add notes to MEMORY.md, or run `memdex bootstrap` later.\n")
    return True


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------
@app.command()
def bootstrap(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the generated memory, write nothing."
    ),
) -> None:
    """Generate a first MEMORY.md from the codebase using a configured LLM."""
    cfg = _load()
    _bootstrap(cfg, dry_run=dry_run)


def _bootstrap(cfg: MemdexConfig, dry_run: bool) -> None:
    from memdex.bootstrap import generate_seed_memory

    if not cfg.llm.is_usable:
        _fail(
            ConfigError(
                "Bootstrap needs an LLM with a large context window.",
                hint=(
                    "Set llm.enabled: true and llm.model in .memdex/config.yaml.\n"
                    "For Ollama: `ollama pull qwen2.5:14b` (a high-context model works best),\n"
                    "then re-run `memdex bootstrap`."
                ),
            )
        )
    _warn_if_remote_llm(cfg)
    output.banner()
    try:
        with acquire(cfg.memdex_dir):
            written = generate_seed_memory(cfg, dry_run=dry_run, progress=output.step)
    except MemdexError as exc:
        _fail(exc)
        return

    if dry_run:
        output.note("\nNothing was written. Run without --dry-run to apply.\n")
        return

    from memdex.store import VectorStore

    audit_log.record(
        cfg,
        VectorStore(cfg),
        audit_log.simple_event(
            "bootstrap",
            f"generated {written} seed memories from the codebase "
            f"({cfg.llm.provider}:{cfg.llm.model})",
            units=written,
            files_written=written,
        ),
    )
    output.step(f"Wrote {written} seed memories to {cfg.output_dir}")
    output.info("\nNow run [accent]memdex run[/accent] to optimize and index them.\n")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
@app.command()
def search(
    query: str = typer.Argument(..., help="What you want to remember."),
    top_k: int = typer.Option(3, "--top-k", "-k", help="How many memories to return."),
    category: str | None = typer.Option(None, "--category", "-c", help="Restrict to one category."),
) -> None:
    """Find memories semantically."""
    from memdex.embed import get_embedder
    from memdex.embed.base import fingerprint
    from memdex.identity import Registry
    from memdex.store import VectorStore

    cfg = _load()
    store = VectorStore(cfg)
    if not store.exists():
        output.error(
            "There is no search index yet.",
            hint="Run `memdex run` to build one from your project memory.",
        )
        raise typer.Exit(code=1)

    try:
        if store.count() == 0:
            output.error(
                "The search index is empty.",
                hint="Run `memdex run` (or `memdex index`) to populate it.",
            )
            raise typer.Exit(code=1)

        registry = Registry.load(cfg.metadata_dir)
        embedder = get_embedder(cfg, warn=output.warn)
        current = fingerprint(embedder)
        if registry.embedder_fingerprint and registry.embedder_fingerprint != current:
            output.warn(
                f"The index was built with {registry.embedder_fingerprint}, but {current} is "
                "configured now — run `memdex index` to rebuild it."
            )
        vector = embedder.embed([query])[0]
        hits = store.query(vector, top_k=top_k, category=category)
    except MemdexError as exc:
        _fail(exc)
        return
    output.search_results(hits, query)


# ---------------------------------------------------------------------------
# status / history / doctor
# ---------------------------------------------------------------------------
@app.command()
def status() -> None:
    """Show what Memdex knows about this project."""
    from memdex.backup import list_backups
    from memdex.pipeline import read_last_run
    from memdex.store import VectorStore

    cfg = _load()
    last_run = read_last_run(cfg)
    files = len(list(cfg.abs_output_dir.rglob("*.md"))) if cfg.abs_output_dir.is_dir() else 0
    vectors = 0
    store = VectorStore(cfg)
    if store.exists():
        try:
            vectors = store.count()
        except MemdexError:
            vectors = 0
    counts = {"files": files, "units": (last_run or {}).get("units", files), "vectors": vectors}
    output.status_table(cfg, last_run, counts, len(list_backups(cfg)))


@app.command()
def history(
    limit: int = typer.Option(20, "--limit", "-n", help="How many events to show."),
) -> None:
    """Show token savings and activity over time (the terminal view of the dashboard)."""
    from memdex.store import VectorStore

    cfg = _load()
    store = VectorStore(cfg)
    if not store.exists():
        output.history_table([])
        return
    output.history_table(audit_log.history(cfg, store, limit=limit))


@app.command()
def doctor() -> None:
    """Check that everything Memdex needs is working."""
    from memdex.store import VectorStore
    from memdex.tokens import get_counter

    cfg = _load()
    rows: list[tuple[str, bool, str]] = []
    ok = True

    rows.append(("Python", True, sys.version.split()[0]))
    rows.append(("Config", True, str(cfg.config_file)))

    try:
        import chromadb

        rows.append(("ChromaDB", True, f"version {chromadb.__version__}"))
    except Exception as exc:  # noqa: BLE001
        ok = False
        rows.append(("ChromaDB", False, f"not importable: {exc}"))

    store = VectorStore(cfg)
    try:
        store.open()
        rows.append(("Vector store", True, f"{cfg.store_path} · {store.count()} vectors"))
    except MemdexError as exc:
        ok = False
        rows.append(("Vector store", False, exc.message))

    counter = get_counter()
    rows.append(
        (
            "Tokenizer",
            True,
            "tiktoken (exact)" if counter.exact else "heuristic — counts are estimates",
        )
    )

    provider_ok, detail = _check_embedder(cfg)
    ok = ok and provider_ok
    rows.append((f"Embeddings ({cfg.embedding_provider})", provider_ok, detail))

    index = cfg.abs_index_path
    if index.is_file():
        from memdex.layout import index_has_marker

        marked = index_has_marker(index.read_text(encoding="utf-8"))
        rows.append(
            (
                "Memory index",
                True,
                "generated by memdex" if marked else "not generated yet — run `memdex run`",
            )
        )
    else:
        rows.append(("Memory index", True, "not created yet — run `memdex run`"))

    if cfg.llm.enabled:
        llm_ok, llm_detail = _check_llm(cfg)
        ok = ok and llm_ok
        rows.append(("LLM", llm_ok, llm_detail))
    else:
        rows.append(("LLM", True, "disabled (deterministic mode)"))

    output.doctor_table(rows)
    if not ok:
        raise typer.Exit(code=1)


def _check_embedder(cfg: MemdexConfig) -> tuple[bool, str]:
    if cfg.embedding_provider == "hash":
        return True, "offline feature hashing — no downloads needed"
    try:
        from memdex.embed import get_embedder
        from memdex.embed.base import fingerprint

        warnings: list[str] = []
        embedder = get_embedder(cfg, warn=warnings.append)
        if warnings:
            return False, warnings[0]
        return True, fingerprint(embedder)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _check_llm(cfg: MemdexConfig) -> tuple[bool, str]:
    if not cfg.llm.model:
        return False, "llm.enabled is true but llm.model is not set"
    if cfg.llm.needs_api_key and not cfg.llm.resolved_api_key:
        return False, (
            f"{cfg.llm.provider} needs an API key, but ${cfg.llm.key_env_name} is not set "
            "in the environment"
        )
    from memdex.llmclient import LLMClient

    client = LLMClient(cfg.llm)
    reachable, detail = client.ping()
    return reachable, detail


# ---------------------------------------------------------------------------
# restore / clean
# ---------------------------------------------------------------------------
@app.command()
def restore(
    timestamp: str | None = typer.Argument(
        None, help="Backup to restore (default: the latest)."
    ),
    latest: bool = typer.Option(False, "--latest", help="Restore the most recent backup."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Put memory files back the way they were before a Memdex run."""
    from memdex.backup import find_backup, list_backups, restore_backup
    from memdex.store import VectorStore

    cfg = _load()
    backups = list_backups(cfg)
    if not backups:
        output.error(
            "There are no backups to restore from.",
            hint="Memdex creates one automatically the first time a run changes a file.",
        )
        raise typer.Exit(code=1)

    if not timestamp and not latest:
        output.info("\nAvailable backups:\n")
        output.backups_table(backups)
        if not _interactive():
            output.note("\nRe-run with a timestamp or --latest.\n")
            raise typer.Exit(code=1)
        timestamp = typer.prompt("\nRestore which backup?", default=backups[0].stamp).strip()

    try:
        info = find_backup(cfg, timestamp, latest)
    except MemdexError as exc:
        _fail(exc)
        return

    total = len(info.backed_up) + len(info.created)
    if not yes and _interactive():
        if not typer.confirm(f"Restore {total} file(s) from {info.stamp}?", default=False):
            output.note("Nothing was restored.")
            return

    with acquire(cfg.memdex_dir):
        result = restore_backup(cfg, info)
        store = VectorStore(cfg)
        if store.exists():
            audit_log.record(
                cfg,
                store,
                audit_log.simple_event(
                    "restore",
                    f"restored {len(result.restored)} file(s) from {info.stamp}",
                    files_written=len(result.restored),
                ),
            )

    output.step(f"Restored {len(result.restored)} file(s) from {info.stamp}")
    if result.removed:
        output.step(f"Removed {len(result.removed)} file(s) Memdex had created")
    if result.pre_restore:
        output.note(f"Previous state saved to {result.pre_restore.dir}")
    output.info("\nRun [accent]memdex index[/accent] to resync the search index.\n")


@app.command()
def clean(
    all_data: bool = typer.Option(
        False, "--all", help="Remove the whole .memdex/ directory, including the activity log."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Wipe the local memory database. Your markdown memory files are never touched."""
    from memdex.store import VectorStore

    cfg = _load()

    if all_data:
        if not yes:
            if not _interactive():
                output.error(
                    "Refusing to remove .memdex/ without confirmation.",
                    hint="Re-run with --all --yes if that is really what you want.",
                )
                raise typer.Exit(code=1)
            output.warn(
                "This removes .memdex/ entirely: config, backups and the activity log go with it."
            )
            if not typer.confirm("Remove everything?", default=False):
                output.note("Nothing was removed.")
                return
        shutil.rmtree(cfg.memdex_dir, ignore_errors=True)
        output.step("Removed .memdex/ — memory files in your project are untouched")
        output.note("Run `memdex init` to start again.\n")
        return

    if not yes and _interactive():
        output.warn("This clears the search index and memory registry (markdown files are kept).")
        if not typer.confirm("Continue?", default=False):
            output.note("Nothing was removed.")
            return

    with acquire(cfg.memdex_dir):
        store = VectorStore(cfg)
        removed_vectors = 0
        if store.exists():
            removed_vectors = store.drop_memories()
        registry_file = cfg.metadata_dir / "memories.json"
        entries = 0
        if registry_file.is_file():
            from memdex.identity import Registry

            entries = len(Registry.load(cfg.metadata_dir).entries)
            registry_file.unlink()
        for name in ("last_run.json",):
            (cfg.metadata_dir / name).unlink(missing_ok=True)
        for name in ("last_index.md", "stats.json"):
            (cfg.index_dir / name).unlink(missing_ok=True)

        # The log outlives the data it describes: a reset is itself history.
        audit_log.record(
            cfg,
            store,
            audit_log.simple_event(
                "clean",
                f"cleaned the memory database: {removed_vectors} vectors and "
                f"{entries} registry entries removed",
                vectors=removed_vectors,
                units=entries,
            ),
        )

    output.step(f"Cleared {removed_vectors} vectors and {entries} registry entries")
    output.note("Markdown memory files were not touched. The activity log kept a record.")
    output.info("\nRun [accent]memdex run[/accent] to rebuild the index.\n")


# ---------------------------------------------------------------------------
# ui
# ---------------------------------------------------------------------------
@app.command()
def ui(
    port: int | None = typer.Option(None, "--port", "-p", help="Port to serve on."),
    host: str | None = typer.Option(
        None, "--host", help="Interface to bind (default 127.0.0.1; use 0.0.0.0 in Docker)."
    ),
    open_browser: bool = typer.Option(False, "--open", help="Open the dashboard in a browser."),
) -> None:
    """Serve the local dashboard showing token savings over time."""
    from memdex.ui.server import serve

    cfg = _load()
    if not cfg.ui.enabled:
        output.error(
            "The dashboard is disabled for this project.",
            hint="Set ui.enabled: true in .memdex/config.yaml, or use `memdex history` instead.",
        )
        raise typer.Exit(code=1)
    try:
        serve(
            cfg,
            port=port or cfg.ui.port,
            open_browser=open_browser,
            host=host or cfg.ui.host,
        )
    except MemdexError as exc:
        _fail(exc)


def _entrypoint() -> None:  # pragma: no cover - exercised through the console script
    try:
        app()
    except MemdexError as exc:
        output.error(exc.message, exc.hint)
        if os.environ.get("MEMDEX_DEBUG"):
            raise
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    _entrypoint()
