from __future__ import annotations

import math
from pathlib import Path

import pytest

from memdex.config import parse_config
from memdex.embed import get_embedder
from memdex.embed.base import fingerprint, label_for
from memdex.embed.hashing import HashEmbedder
from memdex.models import AuditEvent
from memdex.store import VectorRecord, VectorStore, _scalar_metadata
from memdex.tokens import HeuristicCounter, get_counter, reset_counter_cache


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    cfg = parse_config({"version": 1}, tmp_path)
    return VectorStore(cfg)


def record(rid: str, text: str, category: str = "general") -> VectorRecord:
    return VectorRecord(
        id=rid,
        text=text,
        embedding=HashEmbedder().embed([text])[0],
        metadata={
            "memory_id": rid,
            "title": rid.upper(),
            "category": category,
            "source_file": f"memory/{category}/{rid}.md",
            "importance": 0.5,
            "token_count": 10,
            "mode": "managed",
            "nothing": None,
        },
    )


class TestHashEmbedder:
    def test_is_deterministic(self):
        a = HashEmbedder().embed(["we use postgres"])[0]
        b = HashEmbedder().embed(["we use postgres"])[0]
        assert a == b

    def test_is_normalized_and_sized(self):
        vector = HashEmbedder().embed(["anything at all"])[0]
        assert len(vector) == 384
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)

    def test_empty_text_is_still_a_unit_vector(self):
        vector = HashEmbedder().embed([""])[0]
        assert math.isclose(sum(v * v for v in vector), 1.0, rel_tol=1e-9)

    def test_similar_text_is_closer_than_unrelated_text(self):
        embedder = HashEmbedder()
        query, near, far = embedder.embed(
            [
                "why did we choose chroma for vector search",
                "we chose chroma over elasticsearch for vector search",
                "package names are singular and test files end in _test.go",
            ]
        )
        def dot(a, b):
            return sum(x * y for x, y in zip(a, b, strict=True))

        assert dot(query, near) > dot(query, far)

    def test_fingerprint_and_label(self):
        embedder = HashEmbedder()
        assert fingerprint(embedder) == "hash:feature-hash-v1:384"
        assert label_for(embedder) == "Hash (offline)"


class TestEmbedderSelection:
    def test_hash_provider(self, tmp_path: Path):
        cfg = parse_config({"version": 1, "embedding": {"provider": "hash"}}, tmp_path)
        assert isinstance(get_embedder(cfg), HashEmbedder)

    def test_local_falls_back_to_hash_with_a_warning(self, tmp_path: Path):
        # conftest makes the local model unavailable, standing in for a machine
        # with no network and no cached model.
        cfg = parse_config({"version": 1, "embedding": {"provider": "local"}}, tmp_path)
        warnings: list[str] = []
        embedder = get_embedder(cfg, warn=warnings.append)
        assert isinstance(embedder, HashEmbedder)
        assert warnings and "hash embedder" in warnings[0]


class TestVectorStore:
    def test_persists_across_clients(self, store: VectorStore):
        store.sync([record("mem_a", "postgres tenancy")], [])
        reopened = VectorStore(store.cfg)
        assert reopened.count() == 1

    def test_uses_cosine_space(self, store: VectorStore):
        store.sync([record("mem_a", "x")], [])
        assert store.memories.metadata.get("hnsw:space") == "cosine"

    def test_metadata_drops_none_and_keeps_scalars(self):
        clean = _scalar_metadata({"a": None, "b": 1, "c": "x", "d": 0.5, "e": True})
        assert clean == {"b": 1, "c": "x", "d": 0.5, "e": True}

    def test_sync_upserts_and_deletes(self, store: VectorStore):
        store.sync([record("mem_a", "one"), record("mem_b", "two")], [])
        assert store.existing_ids() == {"mem_a", "mem_b"}
        store.sync([record("mem_a", "one updated")], ["mem_b"])
        assert store.existing_ids() == {"mem_a"}

    def test_empty_delete_is_safe(self, store: VectorStore):
        stats = store.sync([], [])
        assert stats.upserted == 0 and stats.deleted == 0

    def test_query_ranks_and_scores(self, store: VectorStore):
        store.sync(
            [
                record("mem_a", "we chose chroma over elasticsearch", "decisions"),
                record("mem_b", "package names are singular", "conventions"),
            ],
            [],
        )
        vector = HashEmbedder().embed(["why chroma instead of elasticsearch"])[0]
        hits = store.query(vector, top_k=2)
        assert hits[0].id == "mem_a"
        assert 0.0 <= hits[0].score <= 1.0
        assert hits[0].score >= hits[1].score
        assert hits[0].path == Path("memory/decisions/mem_a.md")

    def test_category_filter(self, store: VectorStore):
        store.sync(
            [
                record("mem_a", "chroma decision", "decisions"),
                record("mem_b", "chroma deployment", "infrastructure"),
            ],
            [],
        )
        vector = HashEmbedder().embed(["chroma"])[0]
        hits = store.query(vector, top_k=5, category="infrastructure")
        assert [h.id for h in hits] == ["mem_b"]

    def test_fingerprint_change_rebuilds(self, store: VectorStore):
        store.sync([record("mem_a", "one")], [])
        assert store.ensure_fingerprint("hash:feature-hash-v1:384", "local:minilm:384") is True
        assert store.count() == 0

    def test_matching_fingerprint_keeps_the_index(self, store: VectorStore):
        store.sync([record("mem_a", "one")], [])
        assert store.ensure_fingerprint("hash:x:384", "hash:x:384") is False
        assert store.count() == 1

    def test_drop_memories_keeps_audit(self, store: VectorStore):
        store.sync([record("mem_a", "one")], [])
        store.record_event(AuditEvent(id="evt_1", event="run", at="2026-01-01T00:00:00Z"))
        assert store.drop_memories() == 1
        assert store.count() == 0
        assert [e.id for e in store.read_events()] == ["evt_1"]

    def test_events_round_trip_in_order(self, store: VectorStore):
        store.record_event(
            AuditEvent(id="evt_2", event="clean", at="2026-01-02T00:00:00Z", summary="cleaned")
        )
        store.record_event(
            AuditEvent(
                id="evt_1", event="run", at="2026-01-01T00:00:00Z",
                tokens_before=100, tokens_after=40, tokens_saved=60, pct_saved=60.0,
            )
        )
        events = store.read_events()
        assert [e.id for e in events] == ["evt_1", "evt_2"]
        assert events[0].pct_saved == 60.0


def test_store_never_asks_chroma_to_embed():
    """Passing query_texts/documents without vectors makes Chroma download a model."""
    source = (Path(__file__).resolve().parents[2] / "src" / "memdex" / "store.py").read_text()
    assert "query_texts" not in source


class TestTokenCounter:
    def test_heuristic_is_labelled_inexact(self):
        counter = HeuristicCounter()
        assert counter.exact is False
        assert counter.count("abcd" * 10) == 10

    def test_missing_tiktoken_falls_back(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fail(name, *args, **kwargs):
            if name == "tiktoken":
                raise ImportError("no tiktoken")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fail)
        reset_counter_cache()
        assert get_counter().exact is False

    def test_uncached_tiktoken_falls_back(self, monkeypatch):
        """tiktoken can be installed but need the network on first encode."""

        class Boom:
            def encode(self, *args, **kwargs):
                raise RuntimeError("offline")

        import sys
        import types

        module = types.ModuleType("tiktoken")
        module.get_encoding = lambda name: Boom()
        monkeypatch.setitem(sys.modules, "tiktoken", module)
        reset_counter_cache()
        assert get_counter().exact is False
