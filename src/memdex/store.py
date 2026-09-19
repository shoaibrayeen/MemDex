"""The local ChromaDB vector store — the only module that imports chromadb.

Keeping every Chroma call behind this boundary means an upstream API change is a
one-file fix, and it makes the privacy promise checkable: the client is created
with telemetry off, it is never constructed for dry runs, and every call passes
explicit vectors so Chroma never quietly downloads an embedding model of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from memdex.config import MemdexConfig
from memdex.errors import StoreError
from memdex.models import AuditEvent, SearchHit

MEMORY_COLLECTION = "memories"
AUDIT_COLLECTION = "memdex_audit"
BATCH_SIZE = 200
AUDIT_VECTOR = [1.0]  # Chroma requires a vector; audit rows are never searched by similarity.


@dataclass
class VectorRecord:
    id: str
    text: str
    embedding: list[float]
    metadata: dict


@dataclass
class SyncStats:
    upserted: int = 0
    deleted: int = 0
    rebuilt: bool = False
    warnings: list[str] = field(default_factory=list)


def _scalar_metadata(raw: dict) -> dict:
    """Chroma accepts str/int/float/bool only; None must be omitted entirely."""
    clean: dict = {}
    for key, value in raw.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


class VectorStore:
    def __init__(self, cfg: MemdexConfig) -> None:
        self.cfg = cfg
        self._client = None
        self._memories = None
        self._audit = None

    # -- lifecycle ---------------------------------------------------------
    def open(self):
        if self._client is not None:
            return self._client
        try:
            import chromadb
            from chromadb.config import Settings

            self.cfg.chroma_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(self.cfg.chroma_dir),
                settings=Settings(anonymized_telemetry=False, allow_reset=False),
            )
        except Exception as exc:  # noqa: BLE001
            raise StoreError(
                f"Could not open the local vector store at {self.cfg.chroma_dir} "
                f"({exc.__class__.__name__}: {exc}).",
                hint="Run `memdex doctor` for details, or `memdex clean` to rebuild it.",
            ) from exc
        return self._client

    @property
    def memories(self):
        if self._memories is None:
            client = self.open()
            self._memories = client.get_or_create_collection(
                MEMORY_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
        return self._memories

    @property
    def audit(self):
        if self._audit is None:
            client = self.open()
            self._audit = client.get_or_create_collection(AUDIT_COLLECTION)
        return self._audit

    def exists(self) -> bool:
        return self.cfg.chroma_dir.is_dir() and any(self.cfg.chroma_dir.iterdir())

    # -- memories ----------------------------------------------------------
    def ensure_fingerprint(self, expected: str, stored: str | None) -> bool:
        """Drop the memory collection when the vector space changed. True if rebuilt."""
        if stored is None or stored == expected:
            return False
        client = self.open()
        try:
            client.delete_collection(MEMORY_COLLECTION)
        except Exception:  # noqa: BLE001 - absent collection is fine
            pass
        self._memories = None
        return True

    def existing_ids(self) -> set[str]:
        try:
            result = self.memories.get(include=[])
        except Exception as exc:  # noqa: BLE001
            raise StoreError(
                f"Could not read the vector store ({exc.__class__.__name__})."
            ) from exc
        return set(result.get("ids") or [])

    def sync(self, records: list[VectorRecord], stale_ids: list[str]) -> SyncStats:
        stats = SyncStats()
        collection = self.memories

        for start in range(0, len(records), BATCH_SIZE):
            batch = records[start : start + BATCH_SIZE]
            try:
                collection.upsert(
                    ids=[r.id for r in batch],
                    embeddings=[r.embedding for r in batch],
                    documents=[r.text for r in batch],
                    metadatas=[_scalar_metadata(r.metadata) for r in batch],
                )
            except Exception as exc:  # noqa: BLE001
                raise StoreError(
                    f"Could not write to the vector store ({exc.__class__.__name__}: {exc})."
                ) from exc
            stats.upserted += len(batch)

        if stale_ids:  # delete([]) raises on some Chroma versions
            try:
                collection.delete(ids=list(stale_ids))
                stats.deleted = len(stale_ids)
            except Exception as exc:  # noqa: BLE001
                stats.warnings.append(
                    f"Could not remove {len(stale_ids)} stale vectors ({exc.__class__.__name__})."
                )
        return stats

    def query(
        self,
        embedding: list[float],
        top_k: int = 5,
        category: str | None = None,
    ) -> list[SearchHit]:
        collection = self.memories
        where = {"category": category} if category else None
        try:
            result = collection.query(
                query_embeddings=[embedding],
                n_results=max(1, top_k),
                where=where,
                include=["metadatas", "documents", "distances"],
            )
        except Exception as exc:  # noqa: BLE001
            raise StoreError(f"Search failed ({exc.__class__.__name__}: {exc}).") from exc

        ids = (result.get("ids") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        hits: list[SearchHit] = []
        for index, memory_id in enumerate(ids):
            meta = metadatas[index] if index < len(metadatas) else {}
            document = documents[index] if index < len(documents) else ""
            distance = distances[index] if index < len(distances) else 1.0
            hits.append(
                SearchHit(
                    id=str(memory_id),
                    title=str((meta or {}).get("title") or memory_id),
                    path=Path(str((meta or {}).get("source_file") or "")),
                    category=str((meta or {}).get("category") or "general"),
                    # Cosine distance runs 0..2; a plain 1 - d reads as a similarity.
                    score=round(max(0.0, 1.0 - float(distance)), 3),
                    snippet=_snippet(document or ""),
                )
            )
        return hits

    def count(self) -> int:
        try:
            return int(self.memories.count())
        except Exception:  # noqa: BLE001
            return 0

    def drop_memories(self) -> int:
        """Delete the memory collection, keeping the audit trail intact."""
        removed = self.count()
        client = self.open()
        try:
            client.delete_collection(MEMORY_COLLECTION)
        except Exception:  # noqa: BLE001
            pass
        self._memories = None
        return removed

    # -- audit -------------------------------------------------------------
    def record_event(self, event: AuditEvent) -> None:
        try:
            self.audit.upsert(
                ids=[event.id],
                embeddings=[AUDIT_VECTOR],
                documents=[event.summary],
                metadatas=[
                    _scalar_metadata(
                        {
                            "event": event.event,
                            "at": event.at,
                            "tokens_before": event.tokens_before,
                            "tokens_after": event.tokens_after,
                            "tokens_saved": event.tokens_saved,
                            "pct_saved": round(event.pct_saved, 2),
                            "files_written": event.files_written,
                            "units": event.units,
                            "vectors": event.vectors,
                            "mode_label": event.mode_label,
                        }
                    )
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise StoreError(
                f"Could not record the activity log entry ({exc.__class__.__name__})."
            ) from exc

    def read_events(self, limit: int | None = None) -> list[AuditEvent]:
        try:
            result = self.audit.get(include=["metadatas", "documents"])
        except Exception:  # noqa: BLE001
            return []
        events: list[AuditEvent] = []
        ids = result.get("ids") or []
        metadatas = result.get("metadatas") or []
        documents = result.get("documents") or []
        for index, event_id in enumerate(ids):
            meta = metadatas[index] if index < len(metadatas) else {}
            meta = meta or {}
            events.append(
                AuditEvent(
                    id=str(event_id),
                    event=str(meta.get("event", "")),
                    at=str(meta.get("at", "")),
                    summary=documents[index] if index < len(documents) else "",
                    tokens_before=int(meta.get("tokens_before", 0) or 0),
                    tokens_after=int(meta.get("tokens_after", 0) or 0),
                    tokens_saved=int(meta.get("tokens_saved", 0) or 0),
                    pct_saved=float(meta.get("pct_saved", 0.0) or 0.0),
                    files_written=int(meta.get("files_written", 0) or 0),
                    units=int(meta.get("units", 0) or 0),
                    vectors=int(meta.get("vectors", 0) or 0),
                    mode_label=str(meta.get("mode_label", "")),
                )
            )
        events.sort(key=lambda e: e.at)
        if limit:
            return events[-limit:]
        return events


def _snippet(document: str, length: int = 160) -> str:
    flat = " ".join(document.split())
    if len(flat) <= length:
        return flat
    return flat[:length].rsplit(" ", 1)[0] + "…"
