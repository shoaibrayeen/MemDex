from __future__ import annotations

from pathlib import Path

from memdex.identity import (
    Registry,
    RegistryEntry,
    content_hash,
    dedupe_key,
    normalize_aggressive,
    slugify,
)
from tests.conftest import unit


class TestHashing:
    def test_content_hash_ignores_whitespace_drift(self):
        assert content_hash("a\n\n\nb  \n") == content_hash("a\n\nb\n")

    def test_content_hash_is_case_sensitive(self):
        assert content_hash("Postgres") != content_hash("postgres")

    def test_dedupe_key_ignores_case_and_punctuation(self):
        assert dedupe_key("We use Postgres!") == dedupe_key("we use postgres")

    def test_dedupe_key_respects_code(self):
        a = "```\nmake deploy ENV=production\n```"
        b = "```\nmake deploy ENV=staging\n```"
        assert dedupe_key(a) != dedupe_key(b)

    def test_normalize_preserves_fence_text(self):
        assert "ENV=production" in normalize_aggressive("```\nmake deploy ENV=production\n```")


class TestSlugify:
    def test_basic(self):
        assert slugify("Chroma Selection") == "chroma-selection"
        assert slugify("Session storage (deprecated)") == "session-storage-deprecated"

    def test_unicode_and_empty(self):
        assert slugify("Café décisions") == "cafe-decisions"
        assert slugify("!!!") == "untitled"

    def test_length_cap(self):
        assert len(slugify("word " * 40)) <= 60


class TestAdoptionLadder:
    def _registry(self, **entries: RegistryEntry) -> Registry:
        return Registry(entries=dict(entries))

    def test_rung_one_frontmatter_id_wins(self):
        memory = unit("A", "body text here")
        memory.id = "mem_11111111"
        registry = self._registry(mem_11111111=RegistryEntry(content_hash="different"))
        registry.adopt([memory])
        assert memory.id == "mem_11111111"

    def test_rung_two_content_hash_survives_a_move(self):
        memory = unit("New title", "the same body content moved elsewhere")
        registry = self._registry(
            mem_22222222=RegistryEntry(content_hash=memory.content_hash, title="Old title")
        )
        registry.adopt([memory])
        assert memory.id == "mem_22222222"

    def test_rung_three_title_and_category_survive_an_edit(self):
        memory = unit("Database", "a rewritten body")
        memory.category = "architecture"
        registry = self._registry(
            mem_33333333=RegistryEntry(
                content_hash="stale", title="Database", category="architecture"
            )
        )
        registry.adopt([memory])
        assert memory.id == "mem_33333333"

    def test_rung_four_mints_a_new_id(self):
        memory = unit("Brand new", "nothing like this existed before")
        registry = Registry()
        registry.adopt([memory])
        assert memory.id and memory.id.startswith("mem_") and len(memory.id) == 12

    def test_duplicate_claims_are_reminted(self):
        a = unit("A", "body a")
        b = unit("B", "body b")
        a.id = b.id = "mem_44444444"
        registry = Registry()
        registry.adopt([a, b])
        assert a.id != b.id
        assert "mem_44444444" in (a.id, b.id)
        assert registry.warnings

    def test_one_registry_entry_is_claimed_once(self):
        first = unit("Same", "identical body content")
        second = unit("Same", "identical body content")
        registry = self._registry(
            mem_55555555=RegistryEntry(content_hash=first.content_hash, title="Same")
        )
        registry.adopt([first, second])
        assert first.id != second.id


class TestRegistryRoundTrip:
    def test_save_and_load(self, tmp_path: Path):
        registry = Registry(embedder_fingerprint="hash:feature-hash-v1:384")
        memory = unit("A", "body")
        registry.adopt([memory])
        registry.sync_entries([memory], {memory.id: Path("memory/general/a.md")})
        registry.mark_embedded({memory.id}, {memory.id: memory.content_hash})
        registry.save(tmp_path)

        reloaded = Registry.load(tmp_path)
        assert reloaded.embedder_fingerprint == "hash:feature-hash-v1:384"
        assert reloaded.embedded_hash(memory.id) == memory.content_hash
        assert reloaded.entries[memory.id].path == "memory/general/a.md"

    def test_missing_file_is_an_empty_registry(self, tmp_path: Path):
        assert Registry.load(tmp_path / "nope").entries == {}

    def test_corrupt_file_warns_instead_of_raising(self, tmp_path: Path):
        (tmp_path / "memories.json").write_text("{not json", encoding="utf-8")
        registry = Registry.load(tmp_path)
        assert registry.entries == {} and registry.warnings

    def test_stale_ids(self):
        registry = Registry(entries={"mem_a": RegistryEntry(), "mem_b": RegistryEntry()})
        assert registry.stale_ids({"mem_a"}) == ["mem_b"]
