"""The OpenAI-style provider: presets, API keys from the environment, remoteness.

The rule under test everywhere here: the key lives in an environment variable,
reaches the provider only as a request header, and never touches a file or an
error message.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from memdex.cli import app
from memdex.config import LLMConfig, parse_config
from memdex.errors import ConfigError
from memdex.llmclient import LLMClient


def openai_cfg(**kwargs) -> LLMConfig:
    return LLMConfig(enabled=True, provider="openai", model="gpt-4o", **kwargs)


class TestProviderPresets:
    def test_openai_base_url(self, tmp_path: Path):
        cfg = parse_config(
            {"version": 1, "llm": {"enabled": True, "provider": "openai", "model": "gpt-4o"}},
            tmp_path,
        )
        assert cfg.llm.resolved_base_url == "https://api.openai.com/v1"
        assert cfg.llm.is_usable

    def test_openai_key_env_defaults_to_the_convention(self):
        assert openai_cfg().key_env_name == "OPENAI_API_KEY"

    def test_explicit_api_key_env_wins(self, tmp_path: Path):
        cfg = parse_config(
            {"version": 1, "llm": {"provider": "openai", "api_key_env": "SIRION_OPENAI_KEY"}},
            tmp_path,
        )
        assert cfg.llm.key_env_name == "SIRION_OPENAI_KEY"

    def test_other_providers_fall_back_to_the_generic_env(self):
        cfg = LLMConfig(provider="openai-compatible")
        assert cfg.key_env_name == "MEMDEX_LLM_API_KEY"

    def test_only_openai_requires_a_key(self):
        assert openai_cfg().needs_api_key
        assert not LLMConfig(provider="ollama").needs_api_key
        assert not LLMConfig(provider="lmstudio").needs_api_key


class TestKeyResolution:
    def test_key_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        assert openai_cfg().resolved_api_key == "sk-test-123"

    def test_missing_or_blank_key_is_none(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert openai_cfg().resolved_api_key is None
        monkeypatch.setenv("OPENAI_API_KEY", "   ")
        assert openai_cfg().resolved_api_key is None

    def test_a_real_key_in_the_yaml_is_rejected(self, tmp_path: Path):
        with pytest.raises(ConfigError, match="never stores secrets"):
            parse_config(
                {"version": 1, "llm": {"api_key": "sk-proj-abcdef123456789"}}, tmp_path
            )

    def test_the_key_never_lands_in_the_config_template(self, monkeypatch):
        from memdex.config import default_config_yaml

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-should-not-appear")
        assert "sk-test-should-not-appear" not in default_config_yaml()


class TestRemoteDetection:
    @pytest.mark.parametrize(
        "base,remote",
        [
            ("https://api.openai.com/v1", True),
            ("http://localhost:11434/v1", False),
            ("http://127.0.0.1:1234/v1", False),
            ("http://host.docker.internal:11434/v1", False),
            ("https://llm.internal.example.com/v1", True),
        ],
    )
    def test_is_remote(self, base, remote):
        assert LLMConfig(base_url=base).is_remote is remote

    def test_no_endpoint_is_not_remote(self):
        assert LLMConfig(provider="unknown").is_remote is False


class TestAuthorizationHeader:
    def _capture(self, cfg: LLMConfig) -> dict:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "{\"ok\": true}"}}]}
            )

        LLMClient(cfg, transport=httpx.MockTransport(handler)).chat_json("s", "u")
        return seen

    def test_bearer_token_is_sent_when_the_env_has_a_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        assert self._capture(openai_cfg())["auth"] == "Bearer sk-test-123"

    def test_no_header_without_a_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert self._capture(openai_cfg())["auth"] is None

    def test_ping_sends_the_header_too(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"data": []})

        LLMClient(openai_cfg(), transport=httpx.MockTransport(handler)).ping()
        assert seen["auth"] == "Bearer sk-test-123"

    def test_errors_never_carry_the_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-456")

        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        client = LLMClient(openai_cfg(), transport=httpx.MockTransport(refuse))
        with pytest.raises(Exception) as exc:
            client.chat_json("s", "u")
        assert "sk-test-secret-456" not in str(exc.value)
        assert "sk-test-secret-456" not in (getattr(exc.value, "hint", "") or "")


class TestDoctorKeyCheck:
    def _enable_openai(self, root: Path) -> None:
        config = root / ".memdex" / "config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                "llm:\n  enabled: false",
                "llm:\n  enabled: true\n  provider: openai\n  model: gpt-4o\n  timeout: 1",
            ),
            encoding="utf-8",
        )

    def test_missing_key_fails_doctor_with_the_variable_name(
        self, runner, demo_project: Path, monkeypatch
    ):
        self._enable_openai(demo_project)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 1
        assert "OPENAI_API_KEY" in result.output

    def test_run_llm_warns_that_the_endpoint_is_remote(self, runner, demo_project, monkeypatch):
        """The warning must appear before any content is sent."""
        self._enable_openai(demo_project)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")

        # Cut the network off entirely: the warning precedes the first call, and
        # the failed calls fall back to the deterministic result.
        import socket

        def no_network(*args, **kwargs):
            raise OSError("network disabled in tests")

        monkeypatch.setattr(socket, "getaddrinfo", no_network)

        result = runner.invoke(app, ["run", "--llm"])
        flat = " ".join(result.output.split())
        assert result.exit_code == 0, result.output
        assert "endpoint is remote" in flat
        assert "api.openai.com" in flat
        assert "sk-test-123" not in result.output
