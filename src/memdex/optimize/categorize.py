"""Categorization and importance scoring.

Keyword scoring rather than anything clever: it has to be deterministic (the
same memory must land in the same file on every run) and explainable (a user who
disagrees can move the heading). The LLM layer refines these when enabled.
"""

from __future__ import annotations

import re

from memdex.models import CATEGORIES, MemoryUnit

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "architecture": (
        "architecture", "architectural", "service", "services", "component", "module",
        "boundary", "boundaries", "layer", "design", "system", "pipeline", "topology",
        "schema", "database", "storage", "queue", "api", "interface", "data model",
        "microservice", "monolith", "backend", "frontend",
    ),
    "decisions": (
        "decision", "decided", "chose", "chosen", "adr", "rationale", "tradeoff",
        "trade-off", "why we", "instead of", "alternative", "rejected", "selection",
        "we picked", "evaluated",
    ),
    "development": (
        "test", "tests", "testing", "pytest", "jest", "lint", "linting", "build",
        "debug", "workflow", "ci", "cd", "local development", "setup", "install",
        "make", "script", "command", "dev server", "coverage", "pull request",
        "review", "reviewer", "merge", "branch", "commit",
    ),
    "projects": (
        "project", "milestone", "roadmap", "epic", "sprint", "initiative", "phase",
        "deliverable", "launch", "rollout", "backfill",
    ),
    "conventions": (
        "convention", "style", "naming", "format", "formatting", "guideline",
        "standard", "prefer", "always", "never", "must", "should", "rule", "policy",
        "pattern", "idiom",
    ),
    "infrastructure": (
        "infrastructure", "deploy", "deployment", "docker", "kubernetes", "k8s",
        "terraform", "aws", "gcp", "azure", "cluster", "server", "hosting", "env",
        "environment variable", "secret", "monitoring", "observability", "cache",
        "nginx", "helm", "provision",
    ),
    "reference": (
        "reference", "glossary", "link", "docs", "documentation", "endpoint", "url",
        "contact", "credential location", "dashboard", "runbook", "cheat sheet",
    ),
}

WEIGHT_TITLE = 3
WEIGHT_BREADCRUMB = 2
WEIGHT_BODY = 1

# Matched on word boundaries, not as substrings: "ci" lives inside "decisions"
# and "sequencing", which was enough to drag decisions into development.
CATEGORY_PATTERNS: dict[str, re.Pattern[str]] = {
    category: re.compile(
        # A trailing "s" is allowed so a "## Projects" heading still counts as
        # the keyword "project"; the boundaries keep it from matching inside
        # unrelated words.
        r"\b(?:" + "|".join(re.escape(keyword) for keyword in keywords) + r")s?\b",
        re.IGNORECASE,
    )
    for category, keywords in CATEGORY_KEYWORDS.items()
}

OBSOLETE_RE = re.compile(
    r"\b(deprecated|obsolete|no longer (used|valid|needed|true|supported)|"
    r"superseded by|outdated|do not use|don't use anymore)\b",
    re.IGNORECASE,
)

IMPERATIVE_RE = re.compile(r"\b(must|always|never|critical|required|do not|don't)\b", re.IGNORECASE)
FENCE_RE = re.compile(r"^ {0,3}(```|~~~)", re.MULTILINE)


def _score_text(text: str, pattern: re.Pattern[str], weight: int) -> int:
    """Count distinct matching keywords, so one word repeated does not dominate."""
    if not text:
        return 0
    return weight * len({match.group(0).casefold() for match in pattern.finditer(text)})


def categorize(unit: MemoryUnit) -> str:
    breadcrumb = " ".join(unit.breadcrumb)
    scores: dict[str, int] = {}
    for category, pattern in CATEGORY_PATTERNS.items():
        score = (
            _score_text(unit.title, pattern, WEIGHT_TITLE)
            + _score_text(breadcrumb, pattern, WEIGHT_BREADCRUMB)
            + _score_text(unit.body, pattern, WEIGHT_BODY)
        )
        if score:
            scores[category] = score
    if not scores:
        return "general"
    best = max(scores.values())
    # Ties resolve by the fixed CATEGORIES order so the result never depends on
    # dict ordering or input order.
    for category in CATEGORIES:
        if scores.get(category) == best:
            return category
    return "general"


def is_obsolete(unit: MemoryUnit) -> bool:
    return bool(OBSOLETE_RE.search(unit.title) or OBSOLETE_RE.search(unit.body))


def score_importance(unit: MemoryUnit) -> float:
    score = 0.5
    if unit.category in ("decisions", "architecture"):
        score += 0.15
    if IMPERATIVE_RE.search(unit.body) or IMPERATIVE_RE.search(unit.title):
        score += 0.10
    if FENCE_RE.search(unit.body):
        score += 0.05
    if unit.token_count < 30:
        score -= 0.10
    if unit.obsolete:
        score -= 0.15
    return round(min(0.95, max(0.05, score)), 2)
