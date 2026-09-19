"""Project detection: is this a code repo, where is its base, where is .memdex?

Memdex only has something useful to say about software projects, so `init`
refuses elsewhere rather than scattering a vector store through someone's
Documents folder. In a monorepo it also has to decide whether the user meant the
repository root or the submodule they happen to be standing in.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from memdex.config import CONFIG_NAME, MEMDEX_DIR
from memdex.errors import NotACodeProjectError, NotInitializedError

# A manifest is the strongest single signal that a directory is a project root.
MANIFEST_FILES = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "Pipfile",
    "package.json",
    "deno.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "build.sbt",
    "Gemfile",
    "composer.json",
    "mix.exs",
    "CMakeLists.txt",
    "Makefile",
    "Dockerfile",
    "build.zig",
    "pubspec.yaml",
)

MANIFEST_GLOBS = ("*.csproj", "*.sln", "*.xcodeproj")

# Workspace markers mark the base of a monorepo even without .git.
WORKSPACE_MARKERS = (
    "go.work",
    "pnpm-workspace.yaml",
    "lerna.json",
    "nx.json",
    "turbo.json",
    "rush.json",
    "settings.gradle",
    "settings.gradle.kts",
    "Cargo.toml",  # only counts as a workspace marker together with .git/members
)

SOURCE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".kts",
    ".rb", ".php", ".cs", ".c", ".h", ".cc", ".cpp", ".hpp", ".swift", ".m",
    ".scala", ".ex", ".exs", ".sh", ".bash", ".zsh", ".sql", ".vue", ".svelte",
    ".dart", ".lua", ".pl", ".r", ".jl", ".zig", ".clj", ".hs",
}

SKIP_DIRS = {
    ".git", ".hg", ".svn", MEMDEX_DIR, "node_modules", "venv", ".venv", "env",
    "__pycache__", "dist", "build", "target", "vendor", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "site-packages", ".next", ".nuxt", "Pods",
}

MIN_SOURCE_FILES = 3
SCAN_DEPTH = 3


@dataclass
class ProjectSignals:
    is_code_project: bool
    reasons: list[str]
    source_files_found: int


def _has_manifest(directory: Path) -> list[str]:
    found = []
    for name in MANIFEST_FILES:
        if (directory / name).is_file():
            found.append(name)
    for pattern in MANIFEST_GLOBS:
        if any(directory.glob(pattern)):
            found.append(pattern)
    return found


def _count_source_files(directory: Path, limit: int = MIN_SOURCE_FILES) -> int:
    """Count source files up to SCAN_DEPTH, stopping once the limit is reached."""
    count = 0
    stack: list[tuple[Path, int]] = [(directory, 0)]
    while stack and count < limit:
        current, depth = stack.pop()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if count >= limit:
                break
            try:
                if entry.is_dir():
                    if depth + 1 <= SCAN_DEPTH and entry.name not in SKIP_DIRS:
                        if not entry.name.startswith(".") or entry.name in (".github",):
                            stack.append((entry, depth + 1))
                elif entry.suffix.lower() in SOURCE_EXTENSIONS:
                    count += 1
            except OSError:
                continue
    return count


def inspect_project(directory: Path) -> ProjectSignals:
    reasons: list[str] = []
    if (directory / ".git").exists():
        reasons.append("a .git repository")
    manifests = _has_manifest(directory)
    if manifests:
        reasons.append(f"project manifest ({', '.join(manifests[:3])})")
    if (directory / MEMDEX_DIR / CONFIG_NAME).is_file():
        reasons.append("an existing .memdex/ setup")

    source_files = _count_source_files(directory)
    if source_files >= MIN_SOURCE_FILES:
        reasons.append(f"{MIN_SOURCE_FILES}+ source files")

    return ProjectSignals(
        is_code_project=bool(reasons),
        reasons=reasons,
        source_files_found=source_files,
    )


def ensure_code_project(directory: Path, force: bool = False) -> ProjectSignals:
    signals = inspect_project(directory)
    if signals.is_code_project or force:
        return signals
    raise NotACodeProjectError(
        f"{directory} doesn't look like a code project.",
        hint=(
            "Memdex organizes the memory files an AI coding assistant keeps for a software "
            "project (MEMORY.md, memory/, .claude/ …), so it needs a repository to work in.\n"
            "Run it from your project root — or pass --force if this really is one."
        ),
    )


def find_base_dir(start: Path) -> Path | None:
    """Nearest ancestor that looks like the base of a repo/monorepo."""
    start = start.resolve()
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
        markers = (m for m in WORKSPACE_MARKERS if m != "Cargo.toml")
        if any((candidate / marker).exists() for marker in markers):
            return candidate
    return None


def resolve_root(start: Path | None = None) -> Path:
    """Walk up to the nearest directory holding .memdex/ (git-style discovery)."""
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / MEMDEX_DIR / CONFIG_NAME).is_file():
            return candidate
    raise NotInitializedError(
        f"No Memdex project found in {current} or any parent directory.",
        hint="Run `memdex init` from your project root.",
    )


def find_root_or_none(start: Path | None = None) -> Path | None:
    try:
        return resolve_root(start)
    except NotInitializedError:
        return None
