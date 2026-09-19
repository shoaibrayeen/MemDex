from __future__ import annotations

import json
from pathlib import Path

import pytest

from memdex import __version__
from memdex.backup import create_backup, find_backup, list_backups, restore_backup
from memdex.cli import app
from memdex.config import parse_config
from memdex.errors import BackupError
from memdex.indexdoc import render_index
from memdex.layout import diff_ops, plan_docs, render_doc
from memdex.models import FileOp, SourceFile, SourceMode
from memdex.tokens import HeuristicCounter
from tests.conftest import unit

COMMANDS = (
    "init", "run", "compact", "index", "refresh", "bootstrap", "search",
    "status", "history", "doctor", "restore", "clean", "ui",
)


class TestCliSurface:
    def test_help_lists_every_command(self, runner):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for name in COMMANDS:
            assert name in result.output

    def test_version(self, runner):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0 and __version__ in result.output

    @pytest.mark.parametrize("command", COMMANDS)
    def test_each_command_has_help(self, runner, command):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0
        assert command in result.output

    def test_commands_outside_a_project_explain_init(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        for command in ("run", "status", "search"):
            args = [command, "q"] if command == "search" else [command]
            result = runner.invoke(app, args)
            assert result.exit_code == 1
            assert "memdex init" in result.output


@pytest.fixture
def cfg_at(tmp_path: Path):
    def make(**overrides):
        return parse_config({"version": 1, **overrides}, tmp_path)

    return make


class TestRenderDoc:
    def test_round_trips_through_the_parser(self):
        memory = unit("Database choice", "We use Postgres.\n\n```sql\nSELECT 1;\n```")
        memory.id = "mem_12345678"
        memory.category = "decisions"
        memory.description = "Why Postgres."
        memory.provenance = "MEMORY.md > Architecture > Database"

        rendered = render_doc(memory)

        from memdex.parsing import read_memdex_frontmatter, split_frontmatter, strip_title_heading

        data, _, body = split_frontmatter(rendered)
        meta = read_memdex_frontmatter(data)
        assert meta["id"] == "mem_12345678"
        assert meta["category"] == "decisions"
        assert strip_title_heading(body, meta["title"]) == memory.body

    def test_output_has_no_timestamp(self):
        memory = unit("A", "body")
        memory.id = "mem_12345678"
        assert render_doc(memory) == render_doc(memory)
        assert "20" not in render_doc(memory).split("---")[1]


class TestPlanDocs:
    def test_paths_follow_category_and_slug(self, cfg_at):
        memory = unit("Chroma Selection", "body")
        memory.id, memory.category = "mem_12345678", "decisions"
        docs = plan_docs([memory], cfg_at())
        assert docs[0].path == Path("memory/decisions/chroma-selection.md")

    def test_slug_collisions_get_stable_suffixes(self, cfg_at):
        a, b = unit("Testing", "one"), unit("testing", "two")
        a.id, b.id = "mem_aaaaaaaa", "mem_bbbbbbbb"
        a.category = b.category = "development"
        docs = plan_docs([a, b], cfg_at())
        names = sorted(d.path.name for d in docs)
        assert names == ["testing-2.md", "testing.md"]
        assert plan_docs([b, a], cfg_at())[0].path == docs[0].path  # order independent

    def test_readonly_units_are_not_given_output_paths(self, cfg_at):
        memory = unit("A", "body", source_mode=SourceMode.READONLY)
        memory.id = "mem_12345678"
        assert plan_docs([memory], cfg_at()) == []

    def test_shadowed_units_are_skipped(self, cfg_at):
        memory = unit("A", "body")
        memory.id, memory.shadowed_by = "mem_12345678", "mem_other"
        assert plan_docs([memory], cfg_at()) == []


class TestDiffOps:
    def test_no_ops_when_disk_already_matches(self, cfg_at, tmp_path: Path):
        cfg = cfg_at()
        memory = unit("A", "body")
        memory.id = "mem_12345678"
        docs = plan_docs([memory], cfg)
        target = tmp_path / docs[0].path
        target.parent.mkdir(parents=True)
        target.write_text(docs[0].rendered, encoding="utf-8")
        (tmp_path / "MEMORY.md").write_text("index", encoding="utf-8")

        assert diff_ops(docs, "index", [], cfg) == []

    def test_index_only_mode_never_deletes(self, cfg_at, tmp_path: Path):
        cfg = cfg_at()
        source = SourceFile(path=Path("MEMORY.md"), mode=SourceMode.MANAGED, raw_text="x")
        ops = diff_ops([], "new index", [source], cfg, index_only=True)
        assert all(op.action != "delete" for op in ops)

    def test_refuses_to_plan_writes_to_readonly_paths(self, cfg_at, tmp_path: Path):
        cfg = cfg_at(memory={"sources": [{"path": ".claude/", "mode": "readonly"}]})
        memory = unit("A", "body", source_mode=SourceMode.READONLY)
        memory.id = "mem_12345678"
        memory.source_file = Path(".claude/CLAUDE.md")
        source = SourceFile(path=Path(".claude/CLAUDE.md"), mode=SourceMode.READONLY, raw_text="x")
        # A readonly source must never turn into a delete op.
        assert diff_ops([], "", [source], cfg) == []


class TestRenderIndex:
    def _index(self, cfg, *units):
        paths = {u.id: Path(f"memory/{u.category}/{u.id}.md") for u in units}
        return render_index(list(units), paths, cfg, HeuristicCounter())

    def test_groups_by_category_in_a_fixed_order(self, cfg_at):
        a = unit("Arch", "body")
        a.id, a.category = "mem_aaaaaaaa", "architecture"
        d = unit("Dec", "body")
        d.id, d.category = "mem_dddddddd", "decisions"
        text = self._index(cfg_at(), d, a)
        assert text.index("## Architecture") < text.index("## Decisions")

    def test_sorts_by_importance_then_title(self, cfg_at):
        low = unit("Zebra", "body")
        low.id, low.importance, low.category = "mem_11111111", 0.2, "general"
        high = unit("Alpha", "body")
        high.id, high.importance, high.category = "mem_22222222", 0.9, "general"
        text = self._index(cfg_at(), low, high)
        assert text.index("Alpha") < text.index("Zebra")

    def test_empty_when_there_is_nothing_to_index(self, cfg_at):
        assert render_index([], {}, cfg_at(), HeuristicCounter()) == ""

    def test_escapes_brackets_in_titles(self, cfg_at):
        memory = unit("A [weird] title", "body")
        memory.id = "mem_12345678"
        assert r"\[weird\]" in self._index(cfg_at(), memory)

    def test_one_entry_per_readonly_file(self, cfg_at):
        """Memdex cannot split a readonly file, so it must not link to it five times."""
        sections = []
        for index, title in enumerate(("Intro", "Commands", "Rules")):
            memory = unit(f"{title}", "body text", source_mode=SourceMode.READONLY)
            memory.id = f"mem_0000000{index}"
            memory.source_file = Path(".claude/CLAUDE.md")
            memory.document_title = "Working on Memdex"
            memory.line_start = index * 10
            memory.category = "general"
            sections.append(memory)

        paths = {m.id: Path(".claude/CLAUDE.md") for m in sections}
        text = render_index(sections, paths, cfg_at(), HeuristicCounter())

        assert text.count(".claude/CLAUDE.md") == 1
        assert "Working on Memdex" in text  # the file's own title, not a section's
        assert "> 3 memories" in text  # all three are still counted and searchable

    def test_managed_files_are_listed_individually(self, cfg_at):
        first, second = unit("A", "body"), unit("B", "body")
        first.id, second.id = "mem_aaaaaaaa", "mem_bbbbbbbb"
        first.category = second.category = "general"
        paths = {
            "mem_aaaaaaaa": Path("memory/general/a.md"),
            "mem_bbbbbbbb": Path("memory/general/b.md"),
        }
        text = render_index([first, second], paths, cfg_at(), HeuristicCounter())
        assert "memory/general/a.md" in text and "memory/general/b.md" in text


class TestBackups:
    def test_no_ops_means_no_backup(self, cfg_at):
        assert create_backup(cfg_at(), [], reason="run") is None

    def test_manifest_separates_existing_from_created(self, cfg_at, tmp_path: Path):
        cfg = cfg_at()
        (tmp_path / "MEMORY.md").write_text("original", encoding="utf-8")
        ops = [
            FileOp(path=Path("MEMORY.md"), action="overwrite"),
            FileOp(path=Path("memory/a.md"), action="write"),
        ]
        info = create_backup(cfg, ops, reason="run")
        manifest = json.loads((info.dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["backed_up"] == ["MEMORY.md"]
        assert manifest["created"] == ["memory/a.md"]
        assert (info.dir / "MEMORY.md").read_text(encoding="utf-8") == "original"

    def test_restore_puts_files_back_and_removes_new_ones(self, cfg_at, tmp_path: Path):
        cfg = cfg_at()
        (tmp_path / "MEMORY.md").write_text("original", encoding="utf-8")
        info = create_backup(
            cfg,
            [
                FileOp(path=Path("MEMORY.md"), action="overwrite"),
                FileOp(path=Path("memory/a.md"), action="write"),
            ],
            reason="run",
        )
        (tmp_path / "MEMORY.md").write_text("rewritten", encoding="utf-8")
        (tmp_path / "memory").mkdir()
        (tmp_path / "memory" / "a.md").write_text("new", encoding="utf-8")

        result = restore_backup(cfg, info)

        assert (tmp_path / "MEMORY.md").read_text(encoding="utf-8") == "original"
        assert not (tmp_path / "memory" / "a.md").exists()
        assert result.pre_restore is not None

    def test_find_backup_by_prefix_and_latest(self, cfg_at, tmp_path: Path):
        cfg = cfg_at()
        (tmp_path / "MEMORY.md").write_text("x", encoding="utf-8")
        info = create_backup(cfg, [FileOp(path=Path("MEMORY.md"), action="overwrite")], "run")
        assert find_backup(cfg, info.stamp[:10], latest=False).dir == info.dir
        assert find_backup(cfg, None, latest=True).dir == info.dir
        assert len(list_backups(cfg)) == 1

    def test_unknown_backup_name_is_an_error(self, cfg_at, tmp_path: Path):
        cfg = cfg_at()
        (tmp_path / "MEMORY.md").write_text("x", encoding="utf-8")
        create_backup(cfg, [FileOp(path=Path("MEMORY.md"), action="overwrite")], "run")
        with pytest.raises(BackupError):
            find_backup(cfg, "nope", latest=False)


class TestVersioning:
    def test_display_and_package_versions_are_one_release(self):
        """`memdex --version` says 1.0.0-beta; pyproject says 1.0.0b0 — same release."""
        import tomllib
        from packaging.version import Version

        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        packaged = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
        assert Version(packaged) == Version(__version__)

    def test_beta_is_a_prerelease(self):
        from packaging.version import Version

        assert Version(__version__).is_prerelease
