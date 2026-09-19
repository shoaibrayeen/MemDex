"""Stable memory identity: hashes, slugs, IDs and the on-disk registry.

An assistant that cites ``mem_a8f31c`` should still find it after the memory has
been recategorized, retitled or moved, so IDs are adopted through a ladder of
increasingly weak signals rather than derived from the file path.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from memdex.models import MemoryUnit
from memdex.parsing import _fence_spans
from memdex.util import atomic_write, sha256_hex, utc_now_iso

REGISTRY_NAME = "memories.json"
PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
WS_RE = re.compile(r"\s+")


def normalize_light(body: str) -> str:
    """Whitespace-only normalization — what `content_hash` is computed over."""
    lines = [line.rstrip() for line in body.strip().splitlines()]
    out: list[str] = []
    blanks = 0
    for line in lines:
        if not line:
            blanks += 1
            if blanks > 1:
                continue
        else:
            blanks = 0
        out.append(line)
    return "\n".join(out).strip()


def normalize_aggressive(body: str) -> str:
    """Case/punctuation-insensitive form used to spot duplicates.

    Fence interiors keep their exact text: two snippets that differ only by a
    variable name are not the same memory, even though the prose around them is.
    """
    lines = body.splitlines()
    inside = _fence_spans(lines)
    parts: list[str] = []
    for idx, line in enumerate(lines):
        if inside[idx]:
            parts.append(line.strip())
        else:
            stripped = PUNCT_RE.sub(" ", line.casefold())
            parts.append(WS_RE.sub(" ", stripped).strip())
    return WS_RE.sub(" ", " ".join(p for p in parts if p)).strip()


def content_hash(body: str) -> str:
    return sha256_hex(normalize_light(body))


def dedupe_key(body: str) -> str:
    return sha256_hex(normalize_aggressive(body))


def normalize_title(title: str) -> str:
    return WS_RE.sub(" ", PUNCT_RE.sub(" ", title.casefold())).strip()


def slugify(title: str, max_length: int = 60) -> str:
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii").casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or "untitled"


def new_id(existing: set[str] | None = None) -> str:
    existing = existing or set()
    while True:
        candidate = f"mem_{uuid.uuid4().hex[:8]}"
        if candidate not in existing:
            return candidate


@dataclass
class RegistryEntry:
    content_hash: str = ""
    title: str = ""
    category: str = "general"
    description: str = ""
    path: str = ""
    provenance: str = ""
    mode: str = "managed"
    importance: float = 0.5
    token_count: int = 0
    embedded_hash: str | None = None
    last_seen: str = ""


@dataclass
class Registry:
    embedder_fingerprint: str | None = None
    entries: dict[str, RegistryEntry] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    # -- persistence -------------------------------------------------------
    @classmethod
    def load(cls, metadata_dir: Path) -> Registry:
        path = metadata_dir / REGISTRY_NAME
        if not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(warnings=["Memory registry was unreadable; IDs will be reassigned."])
        entries = {
            key: RegistryEntry(
                **{k: v for k, v in value.items() if k in RegistryEntry.__annotations__}
            )
            for key, value in (data.get("entries") or {}).items()
            if isinstance(value, dict)
        }
        return cls(embedder_fingerprint=data.get("embedder_fingerprint"), entries=entries)

    def save(self, metadata_dir: Path) -> None:
        payload = {
            "embedder_fingerprint": self.embedder_fingerprint,
            "entries": {key: asdict(value) for key, value in sorted(self.entries.items())},
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        atomic_write(metadata_dir / REGISTRY_NAME, text)

    # -- adoption ----------------------------------------------------------
    def adopt(self, units: list[MemoryUnit]) -> None:
        """Assign a stable ID to every unit, reusing registry entries where possible."""
        taken: set[str] = set()
        unclaimed = set(self.entries)

        # Pass 1 — units that already carry an ID in their frontmatter.
        for unit in units:
            if not unit.id:
                continue
            if unit.id in taken:
                self.warnings.append(
                    f"Two memories claim the ID {unit.id}; re-minting one for {unit.provenance}."
                )
                unit.id = None
                continue
            taken.add(unit.id)
            unclaimed.discard(unit.id)

        by_hash: dict[str, list[str]] = {}
        by_title: dict[tuple[str, str], list[str]] = {}
        for key in unclaimed:
            entry = self.entries[key]
            by_hash.setdefault(entry.content_hash, []).append(key)
            by_title.setdefault((normalize_title(entry.title), entry.category), []).append(key)

        def claim(pool: list[str]) -> str | None:
            while pool:
                candidate = pool.pop(0)
                if candidate in unclaimed:
                    unclaimed.discard(candidate)
                    return candidate
            return None

        # Pass 2 — identical content that moved.
        for unit in units:
            if unit.id:
                continue
            found = claim(by_hash.get(unit.content_hash, []))
            if found:
                unit.id = found
                taken.add(found)

        # Pass 3 — same title and category, body edited in place.
        for unit in units:
            if unit.id:
                continue
            found = claim(by_title.get((normalize_title(unit.title), unit.category), []))
            if found:
                unit.id = found
                taken.add(found)

        # Pass 4 — genuinely new.
        for unit in units:
            if unit.id:
                continue
            unit.id = new_id(taken | set(self.entries))
            taken.add(unit.id)

    # -- bookkeeping -------------------------------------------------------
    def stale_ids(self, live_ids: set[str]) -> list[str]:
        return sorted(set(self.entries) - live_ids)

    def embedded_hash(self, unit_id: str) -> str | None:
        entry = self.entries.get(unit_id)
        return entry.embedded_hash if entry else None

    def sync_entries(self, units: list[MemoryUnit], paths: dict[str, Path]) -> None:
        """Replace the registry with the given live units (IDs already adopted)."""
        now = utc_now_iso()
        live: dict[str, RegistryEntry] = {}
        for unit in units:
            if not unit.id:
                continue
            previous = self.entries.get(unit.id)
            live[unit.id] = RegistryEntry(
                content_hash=unit.content_hash,
                title=unit.title,
                category=unit.category,
                description=unit.description,
                path=str(paths.get(unit.id, unit.source_file)),
                provenance=unit.provenance,
                mode=unit.source_mode.value,
                importance=unit.importance,
                token_count=unit.token_count,
                embedded_hash=previous.embedded_hash if previous else None,
                last_seen=now,
            )
        self.entries = live

    def mark_embedded(self, unit_ids: set[str], hashes: dict[str, str]) -> None:
        for unit_id in unit_ids:
            entry = self.entries.get(unit_id)
            if entry:
                entry.embedded_hash = hashes.get(unit_id, entry.content_hash)
