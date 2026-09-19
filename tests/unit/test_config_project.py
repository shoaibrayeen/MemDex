from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from memdex.config import default_config_yaml, load_config, parse_config
from memdex.errors import ConfigError, NotACodeProjectError, NotInitializedError
from memdex.models import SourceMode
from memdex.project import ensure_code_project, find_base_dir, inspect_project, resolve_root

# The configuration example from the product specification, verbatim.
SPEC_CONFIG = """
version: 1

memory:
  sources:
    - MEMORY.md
    - memory/
    - .memory/

  output:
    index: MEMORY.md
    directory: memory/

embedding:
  provider: local
  model: default

vector_store:
  provider: chromadb
  path: .memdex/chroma

optimizer:
  mode: deterministic

llm:
  enabled: false
  provider: ollama
  model: null
"""


class TestConfig:
    def test_spec_example_is_valid(self, tmp_path: Path):
        cfg = parse_config(yaml.safe_load(SPEC_CONFIG), tmp_path)
        assert cfg.version == 1
        assert cfg.index_path == "MEMORY.md"
        assert cfg.embedding_provider == "local"
        assert cfg.llm.enabled is False and cfg.llm.model is None
        assert [p for p, _ in cfg.sources] == ["MEMORY.md", "memory/", ".memory/"]

    def test_generated_template_is_valid(self, tmp_path: Path):
        cfg = parse_config(yaml.safe_load(default_config_yaml()), tmp_path)
        assert cfg.ui.enabled is True
        assert any(mode is SourceMode.READONLY for _, mode in cfg.sources)

    def test_no_ui_template(self, tmp_path: Path):
        cfg = parse_config(yaml.safe_load(default_config_yaml(ui_enabled=False)), tmp_path)
        assert cfg.ui.enabled is False

    def test_empty_config_uses_defaults(self, tmp_path: Path):
        cfg = parse_config({}, tmp_path)
        assert cfg.index_path == "MEMORY.md"
        assert cfg.thresholds.duplicate_similarity == 0.85
        assert len(cfg.sources) > 0

    def test_unknown_keys_warn_but_load(self, tmp_path: Path):
        cfg = parse_config({"version": 1, "nonsense": 1}, tmp_path)
        assert cfg.warnings and "nonsense" in cfg.warnings[0]

    def test_source_modes(self, tmp_path: Path):
        cfg = parse_config(
            {
                "version": 1,
                "memory": {
                    "sources": ["MEMORY.md", {"path": ".claude/", "mode": "readonly"}]
                },
            },
            tmp_path,
        )
        assert cfg.sources == [("MEMORY.md", SourceMode.MANAGED), (".claude/", SourceMode.READONLY)]

    @pytest.mark.parametrize(
        "data",
        [
            {"version": 2},
            {"version": 1, "embedding": {"provider": "openai"}},
            {"version": 1, "vector_store": {"provider": "qdrant"}},
            {"version": 1, "memory": {"sources": ["/etc/passwd"]}},
            {"version": 1, "memory": {"sources": ["../outside"]}},
            {"version": 1, "memory": {"sources": [{"path": "x", "mode": "rewrite"}]}},
            {"version": 1, "optimizer": {"duplicate_similarity": 5}},
        ],
    )
    def test_invalid_configs_are_rejected(self, tmp_path: Path, data):
        with pytest.raises(ConfigError):
            parse_config(data, tmp_path)

    def test_llm_base_url_presets(self, tmp_path: Path):
        cfg = parse_config({"version": 1, "llm": {"provider": "lmstudio"}}, tmp_path)
        assert cfg.llm.resolved_base_url == "http://localhost:1234/v1"

    def test_llm_is_usable_needs_model(self, tmp_path: Path):
        cfg = parse_config({"version": 1, "llm": {"enabled": True}}, tmp_path)
        assert cfg.llm.is_usable is False

    def test_load_config_without_init(self, tmp_path: Path):
        with pytest.raises(NotInitializedError):
            load_config(tmp_path)

    def test_broken_yaml_is_a_config_error(self, tmp_path: Path):
        (tmp_path / ".memdex").mkdir()
        (tmp_path / ".memdex" / "config.yaml").write_text("key: [unclosed", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(tmp_path)


class TestProjectDetection:
    def test_manifest_makes_a_code_project(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        assert inspect_project(tmp_path).is_code_project

    def test_source_files_make_a_code_project(self, tmp_path: Path):
        for name in ("a.py", "b.py", "c.py"):
            (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
        assert inspect_project(tmp_path).is_code_project

    def test_a_folder_of_movies_is_not(self, tmp_path: Path):
        for name in ("a.mkv", "b.mp4", "notes.txt"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        assert not inspect_project(tmp_path).is_code_project
        with pytest.raises(NotACodeProjectError):
            ensure_code_project(tmp_path)

    def test_force_overrides_the_guard(self, tmp_path: Path):
        ensure_code_project(tmp_path, force=True)  # does not raise

    def test_existing_memdex_counts_as_a_project(self, tmp_path: Path):
        (tmp_path / ".memdex").mkdir()
        (tmp_path / ".memdex" / "config.yaml").write_text("version: 1\n", encoding="utf-8")
        assert inspect_project(tmp_path).is_code_project


class TestRootResolution:
    def test_finds_base_by_git(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "services" / "api"
        nested.mkdir(parents=True)
        assert find_base_dir(nested) == tmp_path.resolve()

    def test_finds_base_by_workspace_marker(self, tmp_path: Path):
        (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n", encoding="utf-8")
        nested = tmp_path / "packages" / "web"
        nested.mkdir(parents=True)
        assert find_base_dir(nested) == tmp_path.resolve()

    def test_no_base_outside_a_repo(self, tmp_path: Path):
        assert find_base_dir(tmp_path) is None

    def test_resolve_root_walks_up(self, tmp_path: Path):
        (tmp_path / ".memdex").mkdir()
        (tmp_path / ".memdex" / "config.yaml").write_text("version: 1\n", encoding="utf-8")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert resolve_root(nested) == tmp_path.resolve()

    def test_resolve_root_raises_when_absent(self, tmp_path: Path):
        with pytest.raises(NotInitializedError):
            resolve_root(tmp_path)
