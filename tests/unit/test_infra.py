"""Tests for the supporting machinery: locking, atomic writes, providers, server.

These are the pieces that only matter when something goes wrong — a crashed run
leaving a lock behind, a model that will not load, an Ollama server that is not
running — so they are worth testing directly rather than only through the
commands that use them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from memdex.config import LLMConfig, parse_config
from memdex.embed.ollama import OllamaEmbedder
from memdex.errors import EmbedderUnavailable, LockedError, MemdexError
from memdex.llmclient import LLMClient
from memdex.lock import acquire
from memdex.util import (
    atomic_write,
    fmt_int,
    fmt_pct,
    humanize_stem,
    is_within,
    plural,
    relpath_in,
    utc_stamp,
)


class TestLock:
    def test_creates_and_releases(self, tmp_path: Path):
        lock = tmp_path / "lock"
        with acquire(tmp_path):
            assert lock.is_file()
            assert lock.read_text(encoding="utf-8") == str(os.getpid())
        assert not lock.exists()

    def test_released_even_when_the_body_raises(self, tmp_path: Path):
        with pytest.raises(ValueError):
            with acquire(tmp_path):
                raise ValueError("boom")
        assert not (tmp_path / "lock").exists()

    def test_refuses_while_a_live_process_holds_it(self, tmp_path: Path, monkeypatch):
        (tmp_path / "lock").write_text("424242", encoding="utf-8")
        monkeypatch.setattr("memdex.lock._process_alive", lambda pid: True)
        with pytest.raises(LockedError, match="424242"):
            with acquire(tmp_path):
                pass

    def test_clears_a_lock_left_by_a_crashed_run(self, tmp_path: Path, monkeypatch):
        (tmp_path / "lock").write_text("424242", encoding="utf-8")
        monkeypatch.setattr("memdex.lock._process_alive", lambda pid: False)
        with acquire(tmp_path):
            assert (tmp_path / "lock").read_text(encoding="utf-8") == str(os.getpid())

    def test_a_corrupt_lock_file_is_treated_as_stale(self, tmp_path: Path):
        (tmp_path / "lock").write_text("not a pid", encoding="utf-8")
        with acquire(tmp_path):
            pass  # does not raise

    def test_creates_the_directory(self, tmp_path: Path):
        target = tmp_path / "nested" / ".memdex"
        with acquire(target):
            assert target.is_dir()


class TestAtomicWrite:
    def test_creates_parent_directories(self, tmp_path: Path):
        target = tmp_path / "a" / "b" / "c.md"
        atomic_write(target, "hello")
        assert target.read_text(encoding="utf-8") == "hello"

    def test_replaces_existing_content(self, tmp_path: Path):
        target = tmp_path / "f.md"
        atomic_write(target, "one")
        atomic_write(target, "two")
        assert target.read_text(encoding="utf-8") == "two"

    def test_leaves_no_temporary_files_behind(self, tmp_path: Path):
        atomic_write(tmp_path / "f.md", "x")
        assert [p.name for p in tmp_path.iterdir()] == ["f.md"]

    def test_a_failed_write_cleans_up_its_temp_file(self, tmp_path: Path, monkeypatch):
        def explode(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr("memdex.util.os.replace", explode)
        with pytest.raises(OSError):
            atomic_write(tmp_path / "f.md", "x")
        assert list(tmp_path.iterdir()) == []

    def test_writes_unix_line_endings(self, tmp_path: Path):
        target = tmp_path / "f.md"
        atomic_write(target, "a\nb\n")
        assert target.read_bytes() == b"a\nb\n"


class TestPathHelpers:
    def test_is_within(self, tmp_path: Path):
        assert is_within(tmp_path, tmp_path / "a" / "b")
        assert not is_within(tmp_path, tmp_path.parent / "elsewhere")

    def test_relpath_in(self, tmp_path: Path):
        assert relpath_in(tmp_path, tmp_path / "a" / "b.md") == Path("a/b.md")

    def test_relpath_outside_returns_the_original(self, tmp_path: Path):
        outside = tmp_path.parent / "other.md"
        assert relpath_in(tmp_path, outside) == outside

    def test_utc_stamp_is_filesystem_safe(self):
        stamp = utc_stamp()
        assert ":" not in stamp and stamp.endswith("Z")

    @pytest.mark.parametrize(
        "stem,expected",
        [("api-notes", "Api Notes"), ("MEMORY", "Memory"), ("my_app", "My App"), ("", "Untitled")],
    )
    def test_humanize_stem(self, stem, expected):
        assert humanize_stem(stem) == expected

    def test_formatting_helpers(self):
        assert fmt_int(18421) == "18,421"
        assert fmt_pct(73.44) == "73.4%"
        assert plural(1, "duplicate") == "1 duplicate"
        assert plural(3, "duplicate") == "3 duplicates"
        assert plural(2, "memory", "memories") == "2 memories"


class TestOllamaEmbedder:
    def _cfg(self, tmp_path: Path, model: str = "default"):
        return parse_config(
            {
                "version": 1,
                "embedding": {"provider": "ollama", "model": model},
                "llm": {"provider": "ollama"},
            },
            tmp_path,
        )

    def _respond(self, monkeypatch, payload=None, error: Exception | None = None):
        calls: list[dict] = []

        def fake_post(url, json=None, timeout=None):  # noqa: A002 - mirrors httpx
            calls.append({"url": url, "json": json})
            if error is not None:
                raise error
            return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

        monkeypatch.setattr("httpx.post", fake_post)
        return calls

    def test_embeds_and_learns_its_dimension(self, tmp_path: Path, monkeypatch):
        calls = self._respond(monkeypatch, {"embeddings": [[0.1, 0.2, 0.3]]})
        embedder = OllamaEmbedder(self._cfg(tmp_path))
        assert embedder.dim == 3
        assert embedder.model == "nomic-embed-text"  # the default for this provider
        assert calls[0]["url"].endswith("/api/embed")
        assert "/v1" not in calls[0]["url"]  # the native endpoint, not the OpenAI one

    def test_honours_a_configured_model(self, tmp_path: Path, monkeypatch):
        calls = self._respond(monkeypatch, {"embeddings": [[1.0]]})
        OllamaEmbedder(self._cfg(tmp_path, model="mxbai-embed-large"))
        assert calls[0]["json"]["model"] == "mxbai-embed-large"

    def test_empty_input_never_calls_the_server(self, tmp_path: Path, monkeypatch):
        calls = self._respond(monkeypatch, {"embeddings": [[1.0]]})
        embedder = OllamaEmbedder(self._cfg(tmp_path))
        before = len(calls)
        assert embedder.embed([]) == []
        assert len(calls) == before

    def test_an_unreachable_server_is_unavailable(self, tmp_path: Path, monkeypatch):
        self._respond(monkeypatch, error=httpx.ConnectError("refused"))
        with pytest.raises(EmbedderUnavailable, match="Ollama"):
            OllamaEmbedder(self._cfg(tmp_path))

    def test_an_empty_response_is_unavailable(self, tmp_path: Path, monkeypatch):
        self._respond(monkeypatch, {"embeddings": []})
        with pytest.raises(EmbedderUnavailable):
            OllamaEmbedder(self._cfg(tmp_path))

    def test_the_factory_falls_back_to_hash(self, tmp_path: Path, monkeypatch):
        from memdex.embed import get_embedder
        from memdex.embed.hashing import HashEmbedder

        self._respond(monkeypatch, error=httpx.ConnectError("refused"))
        warnings: list[str] = []
        embedder = get_embedder(self._cfg(tmp_path), warn=warnings.append)
        assert isinstance(embedder, HashEmbedder)
        assert warnings


class TestLocalEmbedder:
    def test_a_model_that_will_not_load_is_unavailable(self, monkeypatch, real_local_init):
        """The default provider must report a clear reason, not a raw traceback."""
        import chromadb.utils.embedding_functions as ef

        def explode(*args, **kwargs):
            raise RuntimeError("onnxruntime missing")

        monkeypatch.setattr(ef, "ONNXMiniLM_L6_V2", explode)
        with pytest.raises(EmbedderUnavailable) as exc:
            real_local_init()
        assert "embedding.provider: hash" in exc.value.hint

    def test_a_working_model_reports_its_dimension(self, monkeypatch, real_local_init):
        import chromadb.utils.embedding_functions as ef

        monkeypatch.setattr(ef, "ONNXMiniLM_L6_V2", lambda **kwargs: (lambda texts: [[0.0] * 7]))
        embedder = real_local_init()
        assert embedder.dim == 7
        assert embedder.embed([]) == []


class TestLLMClientPing:
    def _client(self, handler) -> LLMClient:
        cfg = LLMConfig(enabled=True, provider="ollama", model="m")
        return LLMClient(cfg, transport=httpx.MockTransport(handler))

    def test_reachable(self):
        client = self._client(lambda request: httpx.Response(200, json={"data": []}))
        ok, detail = client.ping()
        assert ok and "ollama:m" in detail

    def test_unreachable(self):
        def refuse(request):
            raise httpx.ConnectError("refused")

        ok, detail = self._client(refuse).ping()
        assert not ok and "unreachable" in detail

    def test_error_status(self):
        ok, _ = self._client(lambda request: httpx.Response(500)).ping()
        assert not ok

    def test_missing_base_url_is_an_error(self):
        cfg = LLMConfig(enabled=True, provider="openai-compatible", model="m")
        with pytest.raises(MemdexError, match="base URL"):
            LLMClient(cfg).chat_json("s", "u")


class TestDashboardServer:
    def test_a_taken_port_explains_the_alternative(self, tmp_path: Path):
        from memdex.ui.server import build_server

        cfg = parse_config({"version": 1}, tmp_path)
        first = build_server(cfg, 0)
        try:
            with pytest.raises(MemdexError) as exc:
                build_server(cfg, first.server_address[1])
            assert "--port" in exc.value.hint or "history" in exc.value.hint
        finally:
            first.server_close()

    def test_binds_only_to_loopback(self, tmp_path: Path):
        from memdex.ui.server import build_server

        server = build_server(parse_config({"version": 1}, tmp_path), 0)
        try:
            assert server.server_address[0] == "127.0.0.1"
        finally:
            server.server_close()


def test_module_entry_point_runs():
    """`python -m memdex` must work, not just the console script."""
    result = subprocess.run(
        [sys.executable, "-m", "memdex", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert "memdex" in result.stdout
