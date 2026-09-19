"""What Memdex will and will not read.

Discovery is the blast radius of the whole tool: anything it returns as a
managed source can be rewritten. These tests pin down the rules that keep that
radius small.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memdex.config import parse_config
from memdex.discovery import count_source_roots, discover, ingestible
from memdex.models import INDEX_MARKER, SourceMode


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "memory").mkdir()
    (tmp_path / ".claude").mkdir()
    return tmp_path


def cfg_for(root: Path, **overrides):
    data = {"version": 1}
    data.update(overrides)
    return parse_config(data, root)


def write(root: Path, rel: str, text: str = "content") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestWhatIsRead:
    def test_finds_configured_files_and_directories(self, project: Path):
        write(project, "MEMORY.md")
        write(project, "memory/notes.md")
        write(project, ".claude/CLAUDE.md")
        sources, _ = discover(cfg_for(project))
        assert {str(s.path) for s in sources} == {
            "MEMORY.md",
            "memory/notes.md",
            ".claude/CLAUDE.md",
        }

    def test_records_the_mode_of_each_source(self, project: Path):
        write(project, "MEMORY.md")
        write(project, ".claude/CLAUDE.md")
        modes = {str(s.path): s.mode for s in discover(cfg_for(project))[0]}
        assert modes["MEMORY.md"] is SourceMode.MANAGED
        assert modes[".claude/CLAUDE.md"] is SourceMode.READONLY

    def test_reads_nested_markdown(self, project: Path):
        write(project, "memory/a/b/deep.md")
        assert any(str(s.path) == "memory/a/b/deep.md" for s in discover(cfg_for(project))[0])

    def test_root_claude_and_agents_files_are_readonly_sources(self, project: Path):
        write(project, "CLAUDE.md", "# Rules\n\nRead MEMORY.md first.")
        write(project, "AGENTS.md", "# Agents\n\nRead MEMORY.md first, then search.")
        modes = {str(s.path): s.mode for s in discover(cfg_for(project))[0]}
        assert modes["CLAUDE.md"] is SourceMode.READONLY
        assert modes["AGENTS.md"] is SourceMode.READONLY

    def test_reads_cursor_mdc_rules(self, project: Path):
        write(project, ".cursor/rules/style.mdc", "---\ndescription: x\n---\n# Style\n")
        assert any(s.path.suffix == ".mdc" for s in discover(cfg_for(project))[0])

    def test_ordering_is_deterministic(self, project: Path):
        for name in ("c.md", "a.md", "b.md"):
            write(project, f"memory/{name}")
        first = [str(s.path) for s in discover(cfg_for(project))[0]]
        second = [str(s.path) for s in discover(cfg_for(project))[0]]
        assert first == second == sorted(first)

    def test_managed_sources_come_before_readonly_ones(self, project: Path):
        write(project, ".claude/CLAUDE.md")
        write(project, "memory/notes.md")
        modes = [s.mode for s in discover(cfg_for(project))[0]]
        assert modes == sorted(modes, key=lambda m: m is SourceMode.READONLY)


class TestWhatIsNotRead:
    def test_never_sweeps_the_repository(self, project: Path):
        write(project, "README.md", "# Not memory")
        write(project, "docs/guide.md")
        write(project, "src/notes.md")
        paths = {str(s.path) for s in discover(cfg_for(project))[0]}
        assert paths == set()

    def test_ignores_non_markdown(self, project: Path):
        write(project, "memory/data.json", "{}")
        write(project, "memory/script.py", "x = 1")
        assert discover(cfg_for(project))[0] == []

    def test_never_reads_its_own_directory(self, project: Path):
        write(project, ".memdex/chroma/notes.md")
        write(project, ".memdex/backups/2026/MEMORY.md")
        cfg = cfg_for(project, memory={"sources": [".memdex/", "MEMORY.md"]})
        assert discover(cfg)[0] == []

    def test_skips_files_over_the_size_limit(self, project: Path, monkeypatch):
        write(project, "memory/huge.md", "x" * 100)
        monkeypatch.setattr("memdex.discovery.MAX_FILE_BYTES", 10)
        sources, warnings = discover(cfg_for(project))
        assert sources == []
        assert any("2 MiB" in w or "larger" in w for w in warnings)

    def test_unreadable_files_warn_rather_than_crash(self, project: Path):
        write(project, "memory/binary.md")
        (project / "memory" / "binary.md").write_bytes(b"\xff\xfe\x00invalid utf8 \xc3\x28")
        sources, warnings = discover(cfg_for(project))
        assert sources == []
        assert warnings and "binary.md" in warnings[0]

    def test_missing_sources_are_not_an_error(self, project: Path):
        cfg = cfg_for(project, memory={"sources": ["does-not-exist.md", "nowhere/"]})
        assert discover(cfg) == ([], [])

    def test_a_path_listed_twice_is_read_once(self, project: Path):
        write(project, "MEMORY.md")
        cfg = cfg_for(project, memory={"sources": ["MEMORY.md", "./MEMORY.md"]})
        assert len(discover(cfg)[0]) == 1


class TestGeneratedIndexDetection:
    def test_marks_a_generated_index(self, project: Path):
        write(project, "MEMORY.md", f"{INDEX_MARKER} -->\n# Memory Index\n")
        assert discover(cfg_for(project))[0][0].is_generated_index

    def test_detects_the_marker_in_a_renamed_leftover(self, project: Path):
        """A stale index moved into memory/ must not be ingested as content."""
        write(project, "memory/old-index.md", f"{INDEX_MARKER} -->\n# Memory Index\n")
        source = discover(cfg_for(project))[0][0]
        assert source.is_generated_index
        assert ingestible([source]) == []

    def test_a_marker_deep_in_a_file_does_not_count(self, project: Path):
        write(project, "MEMORY.md", ("filler " * 200) + INDEX_MARKER)
        assert not discover(cfg_for(project))[0][0].is_generated_index

    def test_ingestible_keeps_ordinary_files(self, project: Path):
        write(project, "MEMORY.md", "# Real notes\n")
        sources, _ = discover(cfg_for(project))
        assert ingestible(sources) == sources


class TestAdoptedDocs:
    FRONTMATTER = (
        "---\nmemdex:\n  id: mem_12345678\n  title: A\n  category: decisions\n---\n# A\n\nbody\n"
    )

    def test_generated_file_under_the_output_dir_is_adopted(self, project: Path):
        write(project, "memory/decisions/a.md", self.FRONTMATTER)
        assert discover(cfg_for(project))[0][0].is_adopted_doc

    def test_the_same_frontmatter_elsewhere_is_not_adopted(self, project: Path):
        write(project, ".claude/a.md", self.FRONTMATTER)
        assert not discover(cfg_for(project))[0][0].is_adopted_doc

    def test_a_file_without_a_valid_id_is_not_adopted(self, project: Path):
        write(project, "memory/a.md", "---\nmemdex:\n  id: nope\n---\n# A\n")
        assert not discover(cfg_for(project))[0][0].is_adopted_doc


class TestSourceCounting:
    def test_counts_roots_that_contributed_memory(self, project: Path):
        write(project, "MEMORY.md")
        write(project, "memory/a.md")
        write(project, ".claude/CLAUDE.md")
        cfg = cfg_for(project)
        sources, _ = discover(cfg)
        assert count_source_roots(cfg, sources) == 3

    def test_an_empty_directory_is_not_a_source(self, project: Path):
        (project / ".memory").mkdir()
        cfg = cfg_for(project)
        assert count_source_roots(cfg, discover(cfg)[0]) == 0

    def test_one_file_matched_by_two_config_entries_counts_once(self, project: Path):
        """MEMORY.md and memory.md are the same file on a case-insensitive disk."""
        write(project, "MEMORY.md")
        cfg = cfg_for(project)
        sources, _ = discover(cfg)
        assert len(sources) == 1
        assert count_source_roots(cfg, sources) == 1
