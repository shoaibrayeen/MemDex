from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest
import yaml

from memdex.cli import app
from memdex.config import load_config
from memdex.ui.server import build_server


def run(runner, *args, **kwargs):
    result = runner.invoke(app, list(args), **kwargs)
    assert result.exit_code == 0, result.output
    return result


def flat(text: str) -> str:
    """Console output is wrapped to the terminal width; compare it unwrapped."""
    return " ".join(text.split())


@pytest.fixture
def code_dir(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


class TestInit:
    def test_creates_the_expected_layout(self, runner, code_dir: Path):
        result = run(runner, "init")
        memdex = code_dir / ".memdex"
        for sub in ("config.yaml", "chroma", "index", "metadata", "backups"):
            assert (memdex / sub).exists(), sub
        assert (memdex / ".gitignore").read_text(encoding="utf-8").startswith("*")
        assert "Memdex initialized" in result.output
        assert "Local ChromaDB" in result.output

    def test_config_is_loadable(self, runner, code_dir: Path):
        run(runner, "init")
        cfg = load_config(code_dir)
        assert cfg.version == 1 and cfg.ui.enabled is True

    def test_does_not_touch_existing_memory(self, runner, code_dir: Path):
        memory = code_dir / "MEMORY.md"
        memory.write_text("# Mine\n\nUntouched.\n", encoding="utf-8")
        run(runner, "init")
        assert memory.read_text(encoding="utf-8") == "# Mine\n\nUntouched.\n"

    def test_refuses_outside_a_code_project(self, runner, tmp_path: Path, monkeypatch):
        movies = tmp_path / "Movies"
        movies.mkdir()
        (movies / "film.mkv").write_text("x", encoding="utf-8")
        monkeypatch.chdir(movies)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 1
        assert "code project" in flat(result.output)
        assert not (movies / ".memdex").exists()

    def test_force_overrides_the_guard(self, runner, tmp_path: Path, monkeypatch):
        movies = tmp_path / "Movies"
        movies.mkdir()
        monkeypatch.chdir(movies)
        run(runner, "init", "--force")
        assert (movies / ".memdex" / "config.yaml").is_file()

    def test_refuses_to_clobber_an_existing_setup(self, runner, code_dir: Path):
        run(runner, "init")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 1
        assert "already initialized" in result.output

    def test_offline_flag_selects_the_hash_embedder(self, runner, code_dir: Path):
        run(runner, "init", "--offline")
        assert load_config(code_dir).embedding_provider == "hash"

    def test_no_ui_flag_disables_the_dashboard(self, runner, code_dir: Path):
        run(runner, "init", "--no-ui")
        assert load_config(code_dir).ui.enabled is False


class TestMonorepoChoice:
    @pytest.fixture
    def submodule(self, tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
        base = tmp_path / "mono"
        (base / ".git").mkdir(parents=True)
        sub = base / "services" / "api"
        (sub / "src").mkdir(parents=True)
        (sub / "go.mod").write_text("module api\n", encoding="utf-8")
        monkeypatch.chdir(sub)
        return base, sub

    def test_prompt_choosing_the_repository_root(self, runner, submodule, monkeypatch):
        base, sub = submodule
        monkeypatch.setattr("memdex.cli._interactive", lambda: True)
        result = run(runner, "init", input="1\n")
        assert "larger repository" in result.output
        assert (base / ".memdex").is_dir()
        assert not (sub / ".memdex").exists()

    def test_prompt_choosing_this_directory(self, runner, submodule, monkeypatch):
        base, sub = submodule
        monkeypatch.setattr("memdex.cli._interactive", lambda: True)
        run(runner, "init", input="2\n")
        assert (sub / ".memdex").is_dir()
        assert not (base / ".memdex").exists()

    def test_base_flag_skips_the_prompt(self, runner, submodule):
        base, sub = submodule
        run(runner, "init", "--base")
        assert (base / ".memdex").is_dir()

    def test_here_flag_skips_the_prompt(self, runner, submodule):
        base, sub = submodule
        run(runner, "init", "--here")
        assert (sub / ".memdex").is_dir()

    def test_non_interactive_defaults_to_the_root(self, runner, submodule):
        base, sub = submodule
        result = run(runner, "init")
        assert "Not a terminal" in result.output
        assert (base / ".memdex").is_dir()

    def test_commands_work_from_a_subdirectory(self, runner, submodule, monkeypatch):
        base, sub = submodule
        (base / "MEMORY.md").write_text(
            "# Notes\n\n## Deploys\n\nDeploys run from GitHub Actions on every merge to main.\n",
            encoding="utf-8",
        )
        run(runner, "init", "--base")
        config = base / ".memdex" / "config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace("provider: local", "provider: hash"),
            encoding="utf-8",
        )
        monkeypatch.chdir(sub)
        run(runner, "run")
        assert "Deploys" in run(runner, "search", "deployment").output


class TestDashboard:
    def _serve(self, root: Path):
        server = build_server(load_config(root), 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, f"http://127.0.0.1:{server.server_address[1]}"

    def _get(self, url: str):
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.read().decode("utf-8")

    def test_serves_the_page_and_the_data(self, runner, demo_project: Path):
        run(runner, "run")
        server, url = self._serve(demo_project)
        try:
            page = self._get(url + "/")
            assert "<title>Memdex</title>" in page
            assert "cdn" not in page.lower()  # fully self-contained, works offline

            summary = json.loads(self._get(url + "/api/summary"))
            assert summary["vectors"] > 0
            assert summary["memories"] > 0
            assert summary["categories"]

            history = json.loads(self._get(url + "/api/history"))
            assert history and history[0]["tokens_before"] > 0
        finally:
            server.shutdown()
            server.server_close()

    def test_audit_endpoint_includes_clean_events(self, runner, demo_project: Path):
        run(runner, "run")
        run(runner, "clean", "--yes")
        server, url = self._serve(demo_project)
        try:
            events = json.loads(self._get(url + "/api/audit"))
            assert any(e["event"] == "clean" for e in events)
        finally:
            server.shutdown()
            server.server_close()

    def test_unknown_routes_are_404(self, runner, demo_project: Path):
        server, url = self._serve(demo_project)
        try:
            with pytest.raises(urllib.error.HTTPError) as exc:
                self._get(url + "/api/secrets")
            assert exc.value.code == 404
        finally:
            server.shutdown()
            server.server_close()

    def test_cli_refuses_when_disabled(self, runner, demo_project: Path):
        config = demo_project / ".memdex" / "config.yaml"
        data = yaml.safe_load(config.read_text(encoding="utf-8"))
        data["ui"]["enabled"] = False
        config.write_text(yaml.safe_dump(data), encoding="utf-8")
        result = runner.invoke(app, ["ui"])
        assert result.exit_code == 1
        assert "memdex history" in result.output


class TestOfflineGuarantee:
    def test_a_default_run_and_search_make_no_network_calls(
        self, runner, demo_project: Path, monkeypatch
    ):
        """The default mode must never send memory anywhere."""
        import socket

        class Blocked(socket.socket):
            def __init__(self, *args, **kwargs):
                raise AssertionError("Memdex tried to open a socket in offline mode")

        monkeypatch.setattr(socket, "socket", Blocked)
        monkeypatch.setattr(
            socket,
            "create_connection",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")),
        )

        run(runner, "run")
        run(runner, "search", "database")
        run(runner, "compact")
        run(runner, "status")

    def test_chroma_telemetry_is_disabled(self):
        source = (Path(__file__).resolve().parents[2] / "src" / "memdex" / "store.py").read_text()
        assert "anonymized_telemetry=False" in source
