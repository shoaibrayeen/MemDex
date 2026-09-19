"""Bringing memory back in line with code that moved.

Several teams commit to the same repository; memory written last month can cite
files that were renamed, rewritten or deleted since. ``memdex refresh`` compares
the working tree against the last refresh's git baseline, finds the memories
that reference the code that changed, and — deterministically — flags them.
Nothing is rewritten without a model: changing what a memory *says* is exactly
the kind of edit the deterministic layer must never make. With ``--llm``, each
affected memory is shown the current code and asked to keep, update or mark
itself outdated, and updates are applied with the usual backup first.

Either way the run ends with the normal pipeline pass, so memory files a
teammate edited (and you just pulled) are re-indexed and re-embedded too.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from memdex.config import MemdexConfig
from memdex.errors import LLMError, MemdexError
from memdex.models import FileOp, MemoryUnit
from memdex.project import SKIP_DIRS
from memdex.util import atomic_write, utc_now_iso

BASELINE_NAME = "refresh.json"
MAX_LLM_MEMORIES = 12
MAX_FILE_LINES = 80
MAX_LISTED = 8

# Path-like tokens a memory can cite: src/app/Main.java, pom.xml, config/db.yaml…
PATH_TOKEN_RE = re.compile(
    r"\b(?:[\w.-]+/)*[\w.-]+\."
    r"(?:java|kt|kts|py|go|rs|rb|php|cs|c|h|cc|cpp|hpp|js|jsx|ts|tsx|vue|swift|"
    r"scala|sql|sh|bash|xml|ya?ml|json|toml|ini|properties|gradle|tf|proto)\b"
)

VERIFY_SYSTEM = (
    "You keep developer project memory accurate after the code changed. "
    "For each memory you are given the current state of the files it references. "
    "Reply with JSON only: {\"memories\":[{\"id\":str,\"action\":\"keep|update|obsolete\","
    "\"body\":str,\"description\":str,\"reason\":str}]}. "
    "Use update ONLY when the code contradicts the memory, and then rewrite body as "
    "concise markdown (3-15 lines) stating what is true NOW — never invent facts the "
    "provided code does not support. description is one sentence under 140 characters. "
    "Use obsolete when the subject of the memory no longer exists. Otherwise keep. "
    "Only answer about ids you were given."
)


@dataclass
class ChangedFile:
    status: str  # A / M / D / R (renames carry old_path)
    path: str
    old_path: str | None = None

    @property
    def label(self) -> str:
        names = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "T": "changed"}
        base = names.get(self.status[:1], "changed")
        if self.old_path:
            return f"{self.old_path} → {self.path} ({base})"
        return f"{self.path} ({base})"


@dataclass
class AffectedMemory:
    unit: MemoryUnit
    files: list[ChangedFile] = field(default_factory=list)
    dead_refs: list[str] = field(default_factory=list)


@dataclass
class RefreshReport:
    head: str = ""
    baseline: str | None = None
    baseline_at: str = ""
    first_refresh: bool = False
    changed: list[ChangedFile] = field(default_factory=list)
    affected: list[AffectedMemory] = field(default_factory=list)
    dead: list[AffectedMemory] = field(default_factory=list)
    llm_used: bool = False
    kept: int = 0
    updated: int = 0
    obsolete: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------
def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise MemdexError(
            f"git {' '.join(args[:2])} failed: {result.stderr.strip() or 'unknown error'}",
            hint="`memdex refresh` compares against git history — run it inside a git repository.",
        )
    return result.stdout


def git_head(root: Path) -> str:
    try:
        return _git(root, "rev-parse", "HEAD").strip()
    except MemdexError as exc:
        raise MemdexError(
            "This project has no git history to compare against.",
            hint=(
                "`memdex refresh` diffs the code since the last refresh. Initialize git and "
                "make a commit first — or just run `memdex run` to re-index memory edits."
            ),
        ) from exc


def rev_exists(root: Path, rev: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode == 0


def changed_since(root: Path, since: str) -> list[ChangedFile]:
    """Everything different between ``since`` and the working tree, plus untracked.

    Diffing against the working tree (not HEAD) on purpose: a teammate's pull and
    your own uncommitted edits both count as "the code moved".
    """
    changed: list[ChangedFile] = []
    for line in _git(root, "diff", "--name-status", "--find-renames", since).splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            changed.append(ChangedFile(status="R", path=parts[2], old_path=parts[1]))
        else:
            changed.append(ChangedFile(status=status[:1], path=parts[1]))
    for line in _git(root, "ls-files", "--others", "--exclude-standard").splitlines():
        if line.strip():
            changed.append(ChangedFile(status="A", path=line.strip()))
    return changed


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------
def load_baseline(cfg: MemdexConfig) -> dict | None:
    path = cfg.metadata_dir / BASELINE_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_baseline(cfg: MemdexConfig, head: str, changed: int) -> None:
    cfg.metadata_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(
        cfg.metadata_dir / BASELINE_NAME,
        json.dumps({"commit": head, "at": utc_now_iso(), "changed_files": changed}, indent=2)
        + "\n",
    )


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------
def cited_paths(text: str) -> set[str]:
    return {match.group(0) for match in PATH_TOKEN_RE.finditer(text)}


def _memory_paths(cfg: MemdexConfig) -> set[str]:
    """Paths that are memory, not code — changes there are the pipeline's job."""
    out = {cfg.index_path, cfg.output_dir.rstrip("/")}
    for raw, _ in cfg.sources:
        out.add(raw.rstrip("/"))
    out.add(".memdex")
    return out


def _is_memory_path(path: str, memory_roots: set[str]) -> bool:
    return any(path == root or path.startswith(root + "/") for root in memory_roots)


def _repo_basenames(root: Path, cap: int = 20000) -> set[str]:
    names: set[str] = set()
    count = 0
    for path in root.rglob("*"):
        if count >= cap:
            break
        rel_parts = path.relative_to(root).parts
        if set(rel_parts[:-1]) & SKIP_DIRS:
            continue
        if path.is_file():
            names.add(path.name)
            count += 1
    return names


def code_changes_only(cfg: MemdexConfig, changed: list[ChangedFile]) -> list[ChangedFile]:
    """Drop memory and .memdex paths: those belong to the pipeline, not the code scan."""
    memory_roots = _memory_paths(cfg)
    return [c for c in changed if not _is_memory_path(c.path, memory_roots)]


def find_affected(
    cfg: MemdexConfig, units: list[MemoryUnit], changed: list[ChangedFile]
) -> tuple[list[AffectedMemory], list[AffectedMemory]]:
    """Which live memories cite the changed code, and which cite code that is gone."""
    memory_roots = _memory_paths(cfg)
    code_changes = changed
    basenames = _repo_basenames(cfg.root)

    affected: list[AffectedMemory] = []
    dead: list[AffectedMemory] = []

    for unit in units:
        if not unit.is_live:
            continue
        text = f"{unit.title}\n{unit.description}\n{unit.body}"

        hits = []
        for change in code_changes:
            candidates = [change.path, Path(change.path).name]
            if change.old_path:
                candidates += [change.old_path, Path(change.old_path).name]
            if any(candidate and candidate in text for candidate in candidates):
                hits.append(change)
        if hits:
            affected.append(AffectedMemory(unit=unit, files=hits))

        missing = []
        for token in sorted(cited_paths(text)):
            if _is_memory_path(token, memory_roots):
                continue
            # "dao/<x>_dao.go" cites a *pattern*, not a file: after the
            # placeholder the token starts with "_", so skip those (same for
            # suffix conventions like _test.go).
            if Path(token).name.startswith(("_", ".")):
                continue
            if "/" in token:
                if not (cfg.root / token).exists() and Path(token).name not in basenames:
                    missing.append(token)
            elif token not in basenames:
                missing.append(token)
        if missing:
            dead.append(AffectedMemory(unit=unit, dead_refs=missing))

    return affected, dead


# ---------------------------------------------------------------------------
# LLM verification
# ---------------------------------------------------------------------------
def _file_excerpt(root: Path, change: ChangedFile) -> str:
    target = root / change.path
    if change.status == "D" or not target.is_file():
        return f"### {change.path}\n(deleted — this file no longer exists)"
    try:
        with target.open("r", encoding="utf-8", errors="replace") as handle:
            lines = [next(handle, "") for _ in range(MAX_FILE_LINES)]
    except OSError:
        return f"### {change.path}\n(unreadable)"
    return f"### {change.path} ({change.label})\n" + "".join(lines).strip()


def fixable_candidates(report: RefreshReport) -> list[AffectedMemory]:
    """The flagged memories the model is allowed to rewrite.

    Only managed, Memdex-generated files qualify — a readonly rule in .claude/
    or AGENTS.md that cites changed code is reported, never touched. A memory
    can appear both as affected and as a dead reference; it is verified once,
    with both kinds of evidence.
    """
    merged: dict[str, AffectedMemory] = {}
    for item in [*report.affected, *report.dead]:
        unit = item.unit
        if not (unit.is_managed and unit.frontmatter_locked and unit.id):
            continue
        existing = merged.get(unit.id)
        if existing is None:
            merged[unit.id] = AffectedMemory(
                unit=unit, files=list(item.files), dead_refs=list(item.dead_refs)
            )
        else:
            existing.files.extend(item.files)
            existing.dead_refs.extend(item.dead_refs)
    return list(merged.values())[:MAX_LLM_MEMORIES]


def verify_with_llm(
    cfg: MemdexConfig,
    report: RefreshReport,
    client=None,  # noqa: ANN001 - LLMClient; injectable for tests
) -> list[MemoryUnit]:
    """Ask the model to keep/update/flag each fixable memory. Returns updated units.

    Any provider failure leaves the deterministic report standing.
    """
    from memdex.layout import render_doc
    from memdex.llmclient import LLMClient
    from memdex.optimize.compress import first_sentence

    candidates = fixable_candidates(report)
    if not candidates:
        return []

    client = client or LLMClient(cfg.llm)
    payload = {
        "memories": [
            {
                "id": a.unit.id,
                "title": a.unit.title,
                "body": a.unit.body[:2500],
                "changed_files": [_file_excerpt(cfg.root, c)[:3000] for c in a.files[:3]]
                + [
                    f"### {ref}\n(this referenced file does not exist in the repository)"
                    for ref in a.dead_refs[:2]
                ],
            }
            for a in candidates
        ]
    }

    try:
        data = client.chat_json(VERIFY_SYSTEM, json.dumps(payload))
    except LLMError as exc:
        report.warnings.append(f"{exc.message} The flagged memories were left as they are.")
        return []

    by_id = {a.unit.id: a.unit for a in candidates}
    updated_units: list[MemoryUnit] = []
    for item in data.get("memories") or []:
        if not isinstance(item, dict):
            continue
        unit = by_id.get(item.get("id"))
        if unit is None:
            continue
        action = item.get("action")
        if action == "keep":
            report.kept += 1
        elif action == "obsolete":
            report.obsolete.append(unit.title)
        elif action == "update":
            body = item.get("body")
            if not isinstance(body, str) or not body.strip() or len(body) > 8000:
                report.kept += 1
                continue
            unit.body = body.strip()
            description = item.get("description")
            unit.description = (
                description.strip()
                if isinstance(description, str) and 0 < len(description.strip()) <= 140
                else first_sentence(unit.body)
            )
            updated_units.append(unit)

    if updated_units:
        from memdex.backup import create_backup

        ops = [
            FileOp(path=unit.source_file, action="overwrite", reason="refreshed from code")
            for unit in updated_units
        ]
        create_backup(cfg, ops, reason="refresh")
        for unit in updated_units:
            atomic_write(cfg.root / unit.source_file, render_doc(unit))
        report.updated = len(updated_units)

    return updated_units


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def build_refresh_report(
    cfg: MemdexConfig, since: str | None = None
) -> tuple[RefreshReport, list[MemoryUnit]]:
    from memdex.pipeline import parse_sources
    from memdex.tokens import get_counter

    report = RefreshReport()
    report.head = git_head(cfg.root)

    baseline = load_baseline(cfg)
    requested = since or (baseline or {}).get("commit")
    if requested and rev_exists(cfg.root, requested):
        report.baseline = requested
        report.baseline_at = "" if since else str((baseline or {}).get("at", ""))
    else:
        if requested:
            report.warnings.append(
                f"Baseline {requested[:12]} is gone from history (rebase?); starting fresh."
            )
        report.first_refresh = True

    if report.baseline:
        report.changed = code_changes_only(cfg, changed_since(cfg.root, report.baseline))
    else:
        # First refresh: no diff yet, but the dead-reference scan below still
        # catches memory that cites files which do not exist today.
        report.changed = []

    units = [u for u in parse_sources(cfg, get_counter()).units]
    report.affected, report.dead = find_affected(cfg, units, report.changed)
    return report, units
