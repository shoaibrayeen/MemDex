"""`memdex refresh` — keeping memory honest after the code moves.

The scenario throughout: a team pulls new commits (or edits files locally), and
memory written earlier now describes code that changed or vanished. Everything
here runs against a real git repository built in a temp dir, offline.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from memdex.cli import app
from memdex.config import load_config
from memdex.refresh import (
    changed_since,
    cited_paths,
    git_head,
    load_baseline,
)
from tests.conftest import write_config


def run(runner, *args, **kwargs):
    result = runner.invoke(app, list(args), **kwargs)
    assert result.exit_code == 0, result.output
    return result


def flat(text: str) -> str:
    return " ".join(text.split())


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def team_repo(tmp_path: Path, monkeypatch) -> Path:
    """A committed project whose memory cites real files (and one that is gone)."""
    root = tmp_path / "team"
    (root / "src").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "src" / "app.py").write_text(
        "import psycopg\n\ndef main():\n    return 'served on port 8080'\n", encoding="utf-8"
    )
    (root / "config" / "db.yaml").write_text("engine: postgres\npool: 10\n", encoding="utf-8")
    (root / "MEMORY.md").write_text(
        "# Notes\n\n"
        "## Application entry point\n\n"
        "The service starts from src/app.py and serves HTTP on port 8080, with the\n"
        "database connection pooling handled by psycopg configured at startup.\n\n"
        "## Database configuration\n\n"
        "Connection settings live in config/db.yaml — engine postgres with a pool\n"
        "of ten connections shared across every worker in the deployment.\n\n"
        "## Legacy importer\n\n"
        "The nightly import used to run through legacy/importer.py before it moved\n"
        "into the application itself, and some runbooks may still reference it.\n",
        encoding="utf-8",
    )
    write_config(root)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "initial")
    monkeypatch.chdir(root)
    run_pytest_runner(root)
    return root


def run_pytest_runner(root: Path) -> None:
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["run"])
    assert result.exit_code == 0, result.output


class TestGitPlumbing:
    def test_head_and_changed_files(self, team_repo: Path):
        head = git_head(team_repo)
        (team_repo / "src" / "app.py").write_text("changed = True\n", encoding="utf-8")
        (team_repo / "src" / "new_worker.py").write_text("x = 1\n", encoding="utf-8")
        changes = {c.path: c.status for c in changed_since(team_repo, head)}
        assert changes["src/app.py"] == "M"
        assert changes["src/new_worker.py"] == "A"

    def test_deleted_and_committed_changes(self, team_repo: Path):
        head = git_head(team_repo)
        (team_repo / "config" / "db.yaml").unlink()
        git(team_repo, "add", "-A")
        git(team_repo, "commit", "-qm", "drop db config")
        changes = {c.path: c.status for c in changed_since(team_repo, head)}
        assert changes["config/db.yaml"] == "D"

    def test_no_git_history_is_a_clear_error(self, runner, bare_project: Path):
        result = runner.invoke(app, ["refresh"])
        assert result.exit_code == 1
        assert "git" in flat(result.output)

    def test_cited_paths_finds_code_not_prose(self):
        text = "Settings in config/db.yaml, entry src/app.py; see also pom.xml. Not a.file"
        assert cited_paths(text) == {"config/db.yaml", "src/app.py", "pom.xml"}


class TestFirstRefresh:
    def test_sets_the_baseline_and_scans_dead_refs(self, runner, team_repo: Path):
        result = run(runner, "refresh")
        text = flat(result.output)
        assert "Baseline set to" in text
        # legacy/importer.py never existed — flagged immediately.
        assert "no longer exist" in text and "legacy/importer.py" in text
        baseline = load_baseline(load_config(team_repo))
        assert baseline["commit"] == git_head(team_repo)

    def test_refresh_is_recorded_in_history(self, runner, team_repo: Path):
        run(runner, "refresh")
        assert "refresh" in run(runner, "history").output

    def test_no_duplicate_run_event(self, runner, team_repo: Path):
        """The internal re-index must not add a second 'run' row per refresh."""
        before = run(runner, "history").output.count(" run ")
        run(runner, "refresh")
        assert run(runner, "history").output.count(" run ") == before


class TestDetection:
    def test_modified_file_flags_the_memory_that_cites_it(self, runner, team_repo: Path):
        run(runner, "refresh")  # baseline
        (team_repo / "src" / "app.py").write_text(
            "def main():\n    return 'served on port 9090'\n", encoding="utf-8"
        )
        result = run(runner, "refresh")
        text = flat(result.output)
        assert "1 code change" in text
        assert "Application entry point" in text
        assert "Database configuration" not in text  # untouched memory stays quiet

    def test_uncommitted_changes_count(self, runner, team_repo: Path):
        """Teams also edit locally — refresh diffs the working tree, not HEAD."""
        run(runner, "refresh")
        (team_repo / "config" / "db.yaml").write_text("engine: mysql\n", encoding="utf-8")
        result = run(runner, "refresh")
        assert "Database configuration" in flat(result.output)

    def test_deleting_a_cited_file_flags_it_as_gone(self, runner, team_repo: Path):
        run(runner, "refresh")
        (team_repo / "config" / "db.yaml").unlink()
        git(team_repo, "add", "-A")
        git(team_repo, "commit", "-qm", "remove db.yaml")
        result = run(runner, "refresh")
        text = flat(result.output)
        assert "config/db.yaml" in text
        assert "no longer exist" in text

    def test_since_overrides_the_baseline(self, runner, team_repo: Path):
        run(runner, "refresh")
        first = git_head(team_repo)
        (team_repo / "src" / "app.py").write_text("v2 = True\n", encoding="utf-8")
        git(team_repo, "add", "-A")
        git(team_repo, "commit", "-qm", "v2")
        run(runner, "refresh")  # baseline now at v2
        result = run(runner, "refresh", "--since", first)
        assert "Application entry point" in flat(result.output)

    def test_vanished_baseline_starts_fresh_with_a_warning(self, runner, team_repo: Path):
        run(runner, "refresh")
        cfg = load_config(team_repo)
        stale = json.loads((cfg.metadata_dir / "refresh.json").read_text(encoding="utf-8"))
        stale["commit"] = "0" * 40
        (cfg.metadata_dir / "refresh.json").write_text(json.dumps(stale), encoding="utf-8")
        result = run(runner, "refresh")
        assert "starting fresh" in flat(result.output)

    def test_memory_file_edits_are_resynced_not_reported_as_code(
        self, runner, team_repo: Path
    ):
        """A pulled edit to memory/*.md re-embeds via the pipeline, not the code scan."""
        run(runner, "refresh")
        target = next(team_repo.glob("memory/*/application-entry-point.md"))
        target.write_text(
            target.read_text(encoding="utf-8") + "\nRuns behind nginx in production.\n",
            encoding="utf-8",
        )
        result = run(runner, "refresh")
        text = flat(result.output)
        assert "reference code that changed" not in text
        assert "re-embedded" in text  # the edit still reached the vector store

        from memdex.store import VectorStore

        documents = VectorStore(load_config(team_repo)).memories.get(include=["documents"])
        assert any("nginx" in doc for doc in documents["documents"])

    def test_dry_run_writes_nothing(self, runner, team_repo: Path):
        run(runner, "refresh")
        baseline_before = load_baseline(load_config(team_repo))
        (team_repo / "src" / "app.py").write_text("v3 = 3\n", encoding="utf-8")
        result = run(runner, "refresh", "--dry-run")
        assert "Dry run" in result.output
        assert load_baseline(load_config(team_repo)) == baseline_before


class TestLLMVerification:
    def _client(self, responder):
        from memdex.config import LLMConfig
        from memdex.llmclient import LLMClient

        cfg = LLMConfig(enabled=True, provider="ollama", model="m")
        return LLMClient(cfg, transport=httpx.MockTransport(responder))

    def _verify(self, root: Path, responder):
        from memdex.refresh import build_refresh_report, verify_with_llm

        cfg = load_config(root)
        report, _ = build_refresh_report(cfg)
        assert report.affected, "precondition: something must be affected"
        verify_with_llm(cfg, report, client=self._client(responder))
        return report

    @pytest.fixture
    def repo_with_change(self, runner, team_repo: Path) -> Path:
        run(runner, "refresh")
        (team_repo / "src" / "app.py").write_text(
            "def main():\n    return 'served on port 9090'\n", encoding="utf-8"
        )
        return team_repo

    def test_update_rewrites_the_memory_file_with_a_backup(self, runner, repo_with_change):
        root = repo_with_change

        def responder(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)
            asked = json.loads(sent["messages"][1]["content"])["memories"][0]
            content = {
                "memories": [
                    {
                        "id": asked["id"],
                        "action": "update",
                        "body": "The service starts from src/app.py and serves HTTP on port 9090.",
                        "description": "Entry point src/app.py, now on port 9090.",
                    }
                ]
            }
            return httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
            )

        report = self._verify(root, responder)
        assert report.updated == 1
        target = next(root.glob("memory/*/application-entry-point.md"))
        text = target.read_text(encoding="utf-8")
        assert "9090" in text and "8080" not in text

        from memdex.backup import list_backups

        backups = list_backups(load_config(root))
        assert any(b.reason == "refresh" for b in backups)  # the old body is recoverable

    def test_keep_and_obsolete_change_no_files(self, runner, repo_with_change):
        root = repo_with_change
        before = next(root.glob("memory/*/application-entry-point.md")).read_bytes()

        def responder(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)
            asked = json.loads(sent["messages"][1]["content"])["memories"][0]
            content = {"memories": [{"id": asked["id"], "action": "obsolete"}]}
            return httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
            )

        report = self._verify(root, responder)
        assert report.obsolete == ["Application entry point"]
        assert next(root.glob("memory/*/application-entry-point.md")).read_bytes() == before

    def test_provider_failure_leaves_the_report_standing(self, runner, repo_with_change):
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        report = self._verify(repo_with_change, refuse)
        assert report.updated == 0
        assert report.warnings

    def test_hallucinated_ids_and_oversized_bodies_are_ignored(self, runner, repo_with_change):
        def responder(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)
            asked = json.loads(sent["messages"][1]["content"])["memories"][0]
            content = {
                "memories": [
                    {"id": "mem_ffffffff", "action": "update", "body": "invented"},
                    {"id": asked["id"], "action": "update", "body": "x" * 9000},
                ]
            }
            return httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
            )

        report = self._verify(repo_with_change, responder)
        assert report.updated == 0

    def test_end_to_end_refresh_llm_updates_and_reindexes(
        self, runner, repo_with_change, monkeypatch
    ):
        root = repo_with_change

        def responder(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)
            asked = json.loads(sent["messages"][1]["content"])["memories"][0]
            content = {
                "memories": [
                    {
                        "id": asked["id"],
                        "action": "update",
                        "body": "The service starts from src/app.py on port 9090 behind nginx.",
                        "description": "Entry point, port 9090.",
                    }
                ]
            }
            return httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
            )

        from memdex.llmclient import LLMClient

        real_init = LLMClient.__init__

        def patched(self, cfg, transport=None):
            real_init(self, cfg, transport=httpx.MockTransport(responder))

        monkeypatch.setattr(LLMClient, "__init__", patched)

        config = root / ".memdex" / "config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                "llm:\n  enabled: false", "llm:\n  enabled: true\n  model: m"
            ),
            encoding="utf-8",
        )

        result = run(runner, "refresh", "--llm")
        text = flat(result.output)
        assert "1 updated" in text
        assert "Baseline advanced" in text
        assert "9090" in run(runner, "search", "which port does the service use").output


class TestFixableCandidates:
    def test_managed_dead_reference_is_model_verifiable(self, runner, team_repo: Path):
        """A memory citing a deleted file is exactly what --llm should get to fix."""
        from memdex.config import LLMConfig
        from memdex.llmclient import LLMClient
        from memdex.refresh import build_refresh_report, fixable_candidates, verify_with_llm

        run(runner, "refresh")
        (team_repo / "config" / "db.yaml").unlink()
        git(team_repo, "add", "-A")
        git(team_repo, "commit", "-qm", "drop db.yaml")

        cfg = load_config(team_repo)
        report, _ = build_refresh_report(cfg)
        titles = [c.unit.title for c in fixable_candidates(report)]
        assert "Database configuration" in titles

        def responder(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)
            asked = json.loads(sent["messages"][1]["content"])["memories"]
            target = next(m for m in asked if m["title"] == "Database configuration")
            assert any("does not exist" in c for c in target["changed_files"])
            content = {"memories": [{"id": target["id"], "action": "obsolete"}]}
            return httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
            )

        llm = LLMClient(
            LLMConfig(enabled=True, provider="ollama", model="m"),
            transport=httpx.MockTransport(responder),
        )
        verify_with_llm(cfg, report, client=llm)
        assert "Database configuration" in report.obsolete

    def test_readonly_only_flags_get_the_honest_note(self, runner, tmp_path, monkeypatch):
        """When every flagged memory is a readonly rule, do not advertise --llm."""
        root = tmp_path / "ro"
        (root / "src").mkdir(parents=True)
        (root / ".claude").mkdir()
        (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        (root / ".claude" / "CLAUDE.md").write_text(
            "# Rules\n\n## Graph rule\n\nAlways regenerate tools/graph.json before "
            "planning any change, and read it instead of scanning the sources.\n",
            encoding="utf-8",
        )
        write_config(root)
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        monkeypatch.chdir(root)
        run(runner, "run")

        result = run(runner, "refresh")
        text = flat(result.output)
        assert "tools/graph.json" in text
        assert "readonly sources" in text
        assert "refresh --llm" not in text


class TestNothingToRefresh:
    @pytest.fixture
    def clean_repo(self, tmp_path: Path, monkeypatch) -> Path:
        """A repo whose memory cites only files that exist — no flags anywhere."""
        root = tmp_path / "clean"
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text("PORT = 8080\n", encoding="utf-8")
        (root / "MEMORY.md").write_text(
            "# Notes\n\n## Entry point\n\nThe service starts from src/app.py and serves "
            "HTTP on port 8080 for every deployment environment we run.\n",
            encoding="utf-8",
        )
        write_config(root)
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "initial")
        monkeypatch.chdir(root)
        run_pytest_runner(root)
        return root

    def test_quiet_refresh_says_nothing_to_do(self, runner, clean_repo: Path):
        run(runner, "refresh")  # sets the baseline
        result = run(runner, "refresh")
        text = flat(result.output)
        assert "Nothing to refresh" in text
        assert "no code changes since" in text

    def test_quiet_refreshes_add_no_history_rows(self, runner, clean_repo: Path):
        run(runner, "refresh")
        before = run(runner, "history").output.count("refresh")
        run(runner, "refresh")
        run(runner, "refresh")
        assert run(runner, "history").output.count("refresh") == before

    def test_a_real_change_still_reports_normally(self, runner, clean_repo: Path):
        run(runner, "refresh")
        (clean_repo / "src" / "app.py").write_text("PORT = 9090\n", encoding="utf-8")
        result = run(runner, "refresh")
        assert "Nothing to refresh" not in result.output
        assert "Entry point" in flat(result.output)


class TestPatternSuffixesAreNotDeadRefs:
    def test_placeholder_patterns_are_not_flagged(self, runner, tmp_path, monkeypatch):
        """"dao/<x>_dao.go" documents a convention; "_dao.go" is not a missing file."""
        root = tmp_path / "pat"
        (root / "dao").mkdir(parents=True)
        (root / "dao" / "user_dao.go").write_text("package dao\n", encoding="utf-8")
        (root / "MEMORY.md").write_text(
            "# Notes\n\n## Conventions\n\nDAO pattern: `dao/<x>_dao.go` holds the interface and "
            "`<x>_dao_impl.go` the singleton implementation; suffix tests with `_test.go`.\n",
            encoding="utf-8",
        )
        write_config(root)
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "init")
        monkeypatch.chdir(root)
        run_pytest_runner(root)

        result = run(runner, "refresh")
        assert "no longer exist" not in flat(result.output)
