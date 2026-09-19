"""The prompts.

Anything that asks the user a question has two answers and a non-interactive
path, and all three have to behave. These are the places where getting it wrong
either destroys something or blocks a script.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memdex.cli import app
from tests.conftest import tree_hash


def run(runner, *args, **kwargs):
    result = runner.invoke(app, list(args), **kwargs)
    assert result.exit_code == 0, result.output
    return result


@pytest.fixture
def interactive(monkeypatch):
    monkeypatch.setattr("memdex.cli._interactive", lambda: True)


class TestCleanConfirmation:
    def test_declining_leaves_everything_alone(self, runner, demo_project: Path, interactive):
        run(runner, "run")
        before = tree_hash(demo_project, "MEMORY.md", "memory")

        result = run(runner, "clean", input="n\n")

        assert "Nothing was removed" in result.output
        assert (demo_project / ".memdex" / "metadata" / "memories.json").exists()
        assert tree_hash(demo_project, "MEMORY.md", "memory") == before

    def test_accepting_clears_the_index(self, runner, demo_project: Path, interactive):
        run(runner, "run")
        result = run(runner, "clean", input="y\n")
        assert "Cleared" in result.output
        assert not (demo_project / ".memdex" / "metadata" / "memories.json").exists()

    def test_declining_all_keeps_the_directory(self, runner, demo_project: Path, interactive):
        run(runner, "run")
        result = run(runner, "clean", "--all", input="n\n")
        assert "Nothing was removed" in result.output
        assert (demo_project / ".memdex").is_dir()

    def test_accepting_all_removes_the_directory(self, runner, demo_project: Path, interactive):
        run(runner, "run")
        run(runner, "clean", "--all", input="y\n")
        assert not (demo_project / ".memdex").exists()


class TestRestoreSelection:
    def test_lists_backups_and_restores_the_chosen_one(
        self, runner, demo_project: Path, interactive
    ):
        before = tree_hash(demo_project, "MEMORY.md", "memory")
        run(runner, "run")
        stamp = sorted((demo_project / ".memdex" / "backups").iterdir())[0].name

        result = run(runner, "restore", input=f"{stamp}\ny\n")

        assert "Available backups" in result.output
        assert tree_hash(demo_project, "MEMORY.md", "memory") == before

    def test_declining_the_confirmation_restores_nothing(
        self, runner, demo_project: Path, interactive
    ):
        run(runner, "run")
        after_run = tree_hash(demo_project, "MEMORY.md", "memory")
        result = run(runner, "restore", "--latest", input="n\n")
        assert "Nothing was restored" in result.output
        assert tree_hash(demo_project, "MEMORY.md", "memory") == after_run

    def test_non_interactive_without_a_choice_exits_with_guidance(
        self, runner, demo_project: Path
    ):
        run(runner, "run")
        result = runner.invoke(app, ["restore"])
        assert result.exit_code == 1
        assert "--latest" in result.output


class TestBootstrapOffer:
    def _configure_llm(self, root: Path, base_url: str) -> None:
        config = root / ".memdex" / "config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                "llm:\n  enabled: false",
                "llm:\n  enabled: true\n  model: qwen2.5:14b\n"
                f"  base_url: {base_url}\n  timeout: 1",
            ),
            encoding="utf-8",
        )

    def test_offers_to_bootstrap_when_there_is_no_memory(
        self, runner, bare_project: Path, interactive
    ):
        self._configure_llm(bare_project, "http://127.0.0.1:9/v1")
        result = run(runner, "run", input="n\n")
        assert "Generate one from your codebase" in result.output
        assert "Nothing to do" in result.output

    def test_accepting_the_offer_runs_bootstrap(self, runner, bare_project: Path, interactive):
        """A dead endpoint still proves the offer was accepted and acted on."""
        self._configure_llm(bare_project, "http://127.0.0.1:9/v1")
        result = runner.invoke(app, ["run"], input="y\n")
        assert result.exit_code == 1  # bootstrap needs the model it cannot reach
        assert "Reading the codebase" in result.output

    def test_without_an_llm_it_explains_the_setup(self, runner, bare_project: Path, interactive):
        result = run(runner, "run")
        assert "large context window" in result.output or "high-context" in result.output
        assert "memdex bootstrap" in result.output
        assert not (bare_project / "memory").exists()


class TestMonorepoInitDefault:
    def test_an_unrecognized_choice_falls_back_to_the_root(
        self, runner, tmp_path: Path, monkeypatch, interactive
    ):
        base = tmp_path / "mono"
        (base / ".git").mkdir(parents=True)
        sub = base / "packages" / "web"
        (sub / "src").mkdir(parents=True)
        (sub / "package.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(sub)

        run(runner, "init", input="banana\n")
        assert (base / ".memdex").is_dir()


class TestUiCommand:
    def test_serves_and_stops_cleanly(self, runner, demo_project: Path, monkeypatch):
        """`memdex ui` must hand the config to the server and honour Ctrl+C."""
        seen: dict = {}

        def fake_serve(cfg, port, open_browser=False, host="127.0.0.1"):
            seen["port"] = port
            seen["root"] = cfg.root
            seen["open"] = open_browser
            seen["host"] = host

        monkeypatch.setattr("memdex.ui.server.serve", fake_serve)
        run(runner, "ui", "--port", "8123", "--host", "0.0.0.0")
        assert seen == {"port": 8123, "root": demo_project, "open": False, "host": "0.0.0.0"}

    def test_uses_the_configured_port_by_default(self, runner, demo_project: Path, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr(
            "memdex.ui.server.serve",
            lambda cfg, port, open_browser=False, host="127.0.0.1": seen.update(
                port=port, host=host
            ),
        )
        run(runner, "ui")
        assert seen["port"] == 7644
        assert seen["host"] == "127.0.0.1"  # private by default


class TestLastRunSnapshot:
    def test_records_the_numbers_status_reads_back(self, runner, demo_project: Path):
        run(runner, "run")
        snapshot = json.loads(
            (demo_project / ".memdex" / "metadata" / "last_run.json").read_text(encoding="utf-8")
        )
        assert snapshot["mode"] == "run"
        assert snapshot["tokens_before"] > snapshot["tokens_after"] > 0
        assert snapshot["categories"]
        assert "memdex" not in snapshot  # no accidental nesting

    def test_index_snapshot_is_written_too(self, runner, demo_project: Path):
        run(runner, "run")
        saved = (demo_project / ".memdex" / "index" / "last_index.md").read_text(encoding="utf-8")
        assert saved == (demo_project / "MEMORY.md").read_text(encoding="utf-8")
