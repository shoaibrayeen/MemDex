"""The API key must never reach disk, a log line, or the dashboard.

Memdex reads the key from the environment at call time and sends it only as a
request header. These tests are the guard rails for that: they assert the key is
absent everywhere Memdex writes or displays, and that the repository itself
cannot accidentally carry one.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from memdex.cli import app
from memdex.config import load_config

SECRET = "sk-proj-CANARY0000000000000000000000000000"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def openai_project(demo_project: Path, monkeypatch) -> Path:
    """A project configured for OpenAI, with a canary key in the environment."""
    config = demo_project / ".memdex" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "llm:\n  enabled: false",
            "llm:\n  enabled: true\n  provider: openai\n  model: gpt-4o\n  timeout: 1",
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    return demo_project


def _every_file_under(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


class TestTheKeyNeverReachesDisk:
    def test_no_file_memdex_writes_contains_the_key(self, runner, openai_project: Path):
        """Run the commands that write, then read back everything under .memdex/."""
        for args in (["run"], ["index"], ["compact"], ["refresh", "--no-llm"]):
            runner.invoke(app, args)  # refresh may fail without git; that is fine here

        leaked = []
        for path in _every_file_under(openai_project / ".memdex"):
            try:
                if SECRET in path.read_text(encoding="utf-8", errors="ignore"):
                    leaked.append(str(path.relative_to(openai_project)))
            except OSError:
                continue
        assert leaked == []

    def test_generated_memory_and_index_are_clean(self, runner, openai_project: Path):
        runner.invoke(app, ["run"])
        for path in [openai_project / "MEMORY.md", *openai_project.glob("memory/*/*.md")]:
            assert SECRET not in path.read_text(encoding="utf-8")

    def test_the_config_template_never_carries_a_key(self, monkeypatch):
        from memdex.config import default_config_yaml

        monkeypatch.setenv("OPENAI_API_KEY", SECRET)
        template = default_config_yaml()
        assert SECRET not in template
        assert "api_key_env" in template  # the variable's name is what belongs here


class TestTheKeyNeverReachesOutput:
    def test_no_command_prints_the_key(self, runner, openai_project: Path):
        for args in (["run"], ["status"], ["doctor"], ["history"], ["search", "database"]):
            result = runner.invoke(app, args)
            assert SECRET not in result.output, f"leaked in `memdex {args[0]}`"

    def test_doctor_names_the_variable_not_its_value(self, runner, openai_project, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = runner.invoke(app, ["doctor"])
        assert "OPENAI_API_KEY" in result.output
        assert SECRET not in result.output

    def test_the_llm_label_is_provider_and_model_only(self, openai_project: Path):
        cfg = load_config(openai_project)
        assert cfg.llm.label == "LLM (openai:gpt-4o)"
        assert SECRET not in cfg.llm.label

    def test_the_dashboard_summary_is_clean(self, runner, openai_project: Path):
        from memdex.ui.server import collect_summary

        runner.invoke(app, ["run"])
        payload = json.dumps(collect_summary(load_config(openai_project)))
        assert SECRET not in payload
        assert "openai" in payload  # it does say which provider, which is fine


class TestRepositoryHygiene:
    """The repo must make committing a secret hard, not merely discouraged."""

    @pytest.mark.parametrize(
        "path", [".env", ".env.local", ".env.production", "secrets.pem", "server.key"]
    )
    def test_secret_files_are_git_ignored(self, path):
        result = subprocess.run(
            ["git", "-C", str(REPO), "check-ignore", "-q", path],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, f"{path} would be committable"

    def test_the_vector_store_and_backups_are_ignored(self):
        """They hold memory content and original files — local data, not source."""
        for path in (".memdex/chroma", ".memdex/backups", ".memdex/metadata"):
            result = subprocess.run(
                ["git", "-C", str(REPO), "check-ignore", "-q", path],
                capture_output=True,
                timeout=30,
            )
            assert result.returncode == 0, f"{path} would be committed"

    def test_the_config_is_deliberately_committable(self):
        """It carries no secret, and sharing it is how a team shares its setup."""
        result = subprocess.run(
            ["git", "-C", str(REPO), "check-ignore", "-q", ".memdex/config.yaml"],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode != 0

    def test_no_tracked_file_holds_a_real_looking_key(self):
        """Test fixtures use obvious fakes; nothing should look like a live key."""
        listing = subprocess.run(
            ["git", "-C", str(REPO), "ls-files"],
            capture_output=True, text=True, timeout=60,
        )
        suspicious = []
        for name in listing.stdout.splitlines():
            path = REPO / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for token in ("sk-proj-", "sk-ant-", "sk-"):
                index = text.find(token)
                while index != -1:
                    candidate = text[index : index + 60].split()[0].strip("\"',")
                    body = candidate[len(token):]
                    looks_real = len(body) >= 20 and not any(
                        marker in candidate.lower()
                        for marker in ("test", "fake", "canary", "example", "abcdef", "…", "xxx")
                    )
                    if looks_real:
                        suspicious.append(f"{name}: {candidate[:12]}…")
                    index = text.find(token, index + 1)
        assert suspicious == [], f"possible real keys: {suspicious}"


def test_runner_fixture_available():
    assert isinstance(CliRunner(), CliRunner)
