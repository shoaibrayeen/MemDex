from __future__ import annotations

from pathlib import Path

import yaml

from memdex.cli import app
from memdex.models import INDEX_MARKER
from memdex.parsing import read_memdex_frontmatter, split_frontmatter
from tests.conftest import tree_hash


def run(runner, *args):
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result


class TestRunProducesTheMemoryTree:
    def test_creates_categorized_files_with_identity(self, runner, demo_project: Path):
        run(runner, "run")
        files = sorted(demo_project.glob("memory/*/*.md"))
        assert files, "expected a categorized memory tree"

        for path in files:
            data, _, body = split_frontmatter(path.read_text(encoding="utf-8"))
            meta = read_memdex_frontmatter(data)
            assert meta, f"{path} is missing memdex frontmatter"
            assert meta["id"].startswith("mem_")
            assert meta["title"]
            assert path.parent.name == meta["category"]
            assert body.lstrip().startswith(f"# {meta['title']}")

    def test_ids_are_unique(self, runner, demo_project: Path):
        run(runner, "run")
        ids = []
        for path in demo_project.glob("memory/*/*.md"):
            data, _, _ = split_frontmatter(path.read_text(encoding="utf-8"))
            ids.append(read_memdex_frontmatter(data)["id"])
        assert len(ids) == len(set(ids))

    def test_raw_managed_sources_are_consumed(self, runner, demo_project: Path):
        assert (demo_project / "memory" / "scratch.md").exists()
        run(runner, "run")
        assert not (demo_project / "memory" / "scratch.md").exists()
        # ...but its content survives somewhere in the tree.
        tree = "\n".join(p.read_text(encoding="utf-8") for p in demo_project.glob("memory/*/*.md"))
        assert "staging environment mirrors production" in tree

    def test_readonly_sources_are_never_modified(self, runner, demo_project: Path):
        claude = demo_project / ".claude" / "CLAUDE.md"
        before = claude.read_bytes()
        run(runner, "run")
        assert claude.read_bytes() == before

    def test_code_fences_survive_intact(self, runner, demo_project: Path):
        run(runner, "run")
        tree = "\n".join(p.read_text(encoding="utf-8") for p in demo_project.glob("memory/*/*.md"))
        assert "# Not A Real Heading" in tree  # the fenced heading was not split out
        assert "make deploy ENV=production" in tree

    def test_backup_captures_the_original(self, runner, demo_project: Path):
        original = (demo_project / "MEMORY.md").read_text(encoding="utf-8")
        run(runner, "run")
        backups = sorted((demo_project / ".memdex" / "backups").iterdir())
        assert len(backups) == 1
        saved = (backups[0] / "MEMORY.md").read_text(encoding="utf-8")
        assert saved == original
        manifest = yaml.safe_load((backups[0] / "manifest.json").read_text(encoding="utf-8"))
        assert "MEMORY.md" in manifest["backed_up"]
        assert any(p.startswith("memory/") for p in manifest["created"])


class TestGeneratedIndex:
    def test_is_a_marked_index_not_content(self, runner, demo_project: Path):
        run(runner, "run")
        index = (demo_project / "MEMORY.md").read_text(encoding="utf-8")
        assert index.startswith(INDEX_MARKER)
        assert "# Memory Index" in index
        # Bodies must never appear in the index.
        assert "row-level security is enabled" not in index
        assert "```" not in index

    def test_lists_every_live_memory_with_a_path(self, runner, demo_project: Path):
        run(runner, "run")
        index = (demo_project / "MEMORY.md").read_text(encoding="utf-8")
        for path in demo_project.glob("memory/*/*.md"):
            assert path.relative_to(demo_project).as_posix() in index

    def test_includes_readonly_memories_by_their_own_path(self, runner, demo_project: Path):
        run(runner, "run")
        index = (demo_project / "MEMORY.md").read_text(encoding="utf-8")
        assert ".claude/CLAUDE.md" in index

    def test_index_is_much_smaller_than_the_memory(self, runner, demo_project: Path):
        original = len((demo_project / "MEMORY.md").read_text(encoding="utf-8"))
        run(runner, "run")
        assert len((demo_project / "MEMORY.md").read_text(encoding="utf-8")) < original

    def test_second_run_does_not_reingest_the_index(self, runner, demo_project: Path):
        run(runner, "run")
        first = (demo_project / "MEMORY.md").read_text(encoding="utf-8")
        run(runner, "run")
        assert (demo_project / "MEMORY.md").read_text(encoding="utf-8") == first
        assert "Memory Index" not in "\n".join(
            p.read_text(encoding="utf-8") for p in demo_project.glob("memory/*/*.md")
        )


class TestDeduplication:
    def test_duplicate_sections_collapse(self, runner, demo_project: Path):
        result = run(runner, "run")
        assert "duplicate" in result.output
        databases = list(demo_project.glob("memory/*/database*.md"))
        assert len(databases) == 1

    def test_readonly_duplicate_loses_to_managed(self, runner, demo_project: Path):
        run(runner, "run")
        index = (demo_project / "MEMORY.md").read_text(encoding="utf-8")
        # "Chroma selection" exists in both MEMORY.md and .claude/; only the
        # managed copy should be indexed.
        assert index.count("Chroma selection") == 1
        assert "memory/decisions/chroma-selection.md" in index


class TestTokenReporting:
    def test_reports_before_after_and_percentage(self, runner, demo_project: Path):
        result = run(runner, "run")
        assert "Before:" in result.output and "After:" in result.output
        assert "Saved:" in result.output and "%" in result.output

    def test_estimated_label_without_an_exact_tokenizer(self, runner, demo_project, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fail(name, *args, **kwargs):
            if name == "tiktoken":
                raise ImportError
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fail)
        from memdex.tokens import reset_counter_cache

        reset_counter_cache()
        result = run(runner, "run")
        assert "estimated" in result.output


class TestIdempotency:
    def test_second_run_changes_nothing(self, runner, demo_project: Path):
        run(runner, "run")
        before = tree_hash(demo_project, "MEMORY.md", "memory")
        ids_before = _ids(demo_project)
        backups_before = len(list((demo_project / ".memdex" / "backups").iterdir()))

        result = run(runner, "run")

        assert tree_hash(demo_project, "MEMORY.md", "memory") == before
        assert _ids(demo_project) == ids_before
        assert "already optimized" in result.output
        assert len(list((demo_project / ".memdex" / "backups").iterdir())) == backups_before

    def test_second_run_embeds_nothing_new(self, runner, demo_project: Path):
        run(runner, "run")
        result = run(runner, "run")
        assert "local embedding" not in result.output

    def test_vector_count_is_stable(self, runner, demo_project: Path):
        run(runner, "run")
        first = _vector_count(demo_project)
        run(runner, "run")
        assert _vector_count(demo_project) == first


class TestEmbeddingText:
    def test_title_and_breadcrumb_are_embedded_with_the_body(self):
        from memdex.pipeline import embedding_text
        from tests.conftest import unit

        memory = unit("Testing", "Run make test before every commit.")
        memory.breadcrumb = ("Development",)
        text = embedding_text(memory)
        assert text.startswith("Testing")
        assert "Development" in text and "make test" in text

    def test_generated_files_embed_the_same_text_on_re_read(self, runner, demo_project: Path):
        """A memory must not change vector between the run that wrote it and the next."""
        from memdex.config import load_config
        from memdex.pipeline import embedding_text, parse_sources
        from memdex.tokens import get_counter

        run(runner, "run")
        cfg = load_config(demo_project)
        after = {
            u.title: embedding_text(u) for u in parse_sources(cfg, get_counter()).units if u.is_live
        }
        run(runner, "run")
        again = {
            u.title: embedding_text(u) for u in parse_sources(cfg, get_counter()).units if u.is_live
        }
        assert after == again


class TestIdStability:
    def test_editing_a_memory_keeps_its_id(self, runner, demo_project: Path):
        run(runner, "run")
        target = next(demo_project.glob("memory/*/database*.md"))
        data, _, _ = split_frontmatter(target.read_text(encoding="utf-8"))
        original = read_memdex_frontmatter(data)["id"]

        target.write_text(
            target.read_text(encoding="utf-8") + "\nWe also run a read replica.\n",
            encoding="utf-8",
        )
        run(runner, "run")

        after = next(demo_project.glob("memory/*/database*.md"))
        data, _, body = split_frontmatter(after.read_text(encoding="utf-8"))
        assert read_memdex_frontmatter(data)["id"] == original
        assert "read replica" in body

    def test_moving_a_memory_keeps_its_id(self, runner, demo_project: Path):
        run(runner, "run")
        target = next(demo_project.glob("memory/*/database*.md"))
        data, _, _ = split_frontmatter(target.read_text(encoding="utf-8"))
        original = read_memdex_frontmatter(data)["id"]

        moved = demo_project / "memory" / "reference" / "moved.md"
        moved.parent.mkdir(parents=True, exist_ok=True)
        moved.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        target.unlink()
        run(runner, "run")

        assert original in _ids(demo_project)


def _ids(root: Path) -> set[str]:
    out = set()
    for path in root.glob("memory/*/*.md"):
        data, _, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        meta = read_memdex_frontmatter(data)
        if meta:
            out.add(meta["id"])
    return out


def _vector_count(root: Path) -> int:
    from memdex.config import load_config
    from memdex.store import VectorStore

    return VectorStore(load_config(root)).count()
