"""The optional LLM layer, tested entirely against mocked HTTP.

No test here contacts a real model: the point of these cases is that every way
an LLM can let Memdex down — unreachable, slow, chatty, malformed, out of
contract — leaves the deterministic result standing and the command succeeding.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from memdex.bootstrap import generate_seed_memory, validate_memories
from memdex.cli import app
from memdex.config import LLMConfig, load_config
from memdex.errors import LLMError, MemdexError
from memdex.llmclient import LLMClient, parse_json_object
from memdex.optimize.base import OptimizeContext
from memdex.optimize.deterministic import DeterministicOptimizer
from memdex.optimize.llm import LLMOptimizer
from memdex.tokens import HeuristicCounter
from tests.conftest import unit


def llm_cfg(**kwargs) -> LLMConfig:
    return LLMConfig(enabled=True, provider="ollama", model="qwen2.5:14b", **kwargs)


def flat(text: str) -> str:
    """Console output is wrapped to the terminal width; compare it unwrapped."""
    return " ".join(text.split())


def client_returning(*payloads, status: int = 200) -> LLMClient:
    """A client whose Nth call returns the Nth payload (the last one repeats)."""
    queue = list(payloads)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(payload, Exception):
            raise payload
        content = payload if isinstance(payload, str) else json.dumps(payload)
        return httpx.Response(
            status, json={"choices": [{"message": {"content": content}}]}
        )

    return LLMClient(llm_cfg(), transport=httpx.MockTransport(handler))


def optimize_with(client: LLMClient, units, cfg):
    optimizer = LLMOptimizer(DeterministicOptimizer(), client, llm_cfg())
    warnings: list[str] = []
    ctx = OptimizeContext(cfg=cfg, counter=HeuristicCounter(), warn=warnings.append)
    result, analysis = optimizer.optimize(units, ctx)
    return result, analysis, warnings


class TestJsonParsing:
    def test_plain_object(self):
        assert parse_json_object('{"a": 1}') == {"a": 1}

    def test_fenced_object(self):
        assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}

    def test_object_with_chatter_around_it(self):
        assert parse_json_object('Sure! {"a": 1} Hope that helps.') == {"a": 1}

    @pytest.mark.parametrize("bad", ["not json at all", "{broken", "[1,2]", ""])
    def test_rejects_anything_else(self, bad):
        with pytest.raises(LLMError):
            parse_json_object(bad)


class TestLLMOptimizer:
    def test_applies_valid_refinements(self, cfg):
        memory = unit("db", "We use PostgreSQL 16 with row level security for each tenant.")
        client = client_returning(
            {
                "units": [
                    {
                        "id": None,  # filled in below
                        "title": "Database architecture",
                        "description": "Postgres 16 with row-level security per tenant.",
                        "category": "architecture",
                        "importance": 0.9,
                        "obsolete": False,
                    }
                ]
            }
        )
        memory.id = "mem_12345678"
        client._transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "units": [
                                            {
                                                "id": "mem_12345678",
                                                "title": "Database architecture",
                                                "description": "Postgres 16 with RLS per tenant.",
                                                "category": "architecture",
                                                "importance": 0.9,
                                                "obsolete": False,
                                            }
                                        ]
                                    }
                                )
                            }
                        }
                    ]
                },
            )
        )
        units, _, warnings = optimize_with(client, [memory], cfg)
        assert units[0].title == "Database architecture"
        assert units[0].description == "Postgres 16 with RLS per tenant."
        assert units[0].importance == 0.9
        assert not warnings

    def test_invalid_fields_keep_the_deterministic_value(self, cfg):
        memory = unit("Database", "We use PostgreSQL 16 for every tenant in the system.")
        memory.id = "mem_12345678"
        client = client_returning(
            {
                "units": [
                    {
                        "id": "mem_12345678",
                        "title": "x" * 200,          # too long
                        "category": "not-a-category",  # not in the vocabulary
                        "importance": 42,              # out of range
                        "obsolete": "maybe",           # not a boolean
                    }
                ]
            }
        )
        units, _, _ = optimize_with(client, [memory], cfg)
        assert units[0].title == "Database"
        assert units[0].category in ("architecture", "general")
        assert 0.05 <= units[0].importance <= 0.95
        assert units[0].obsolete is False

    def test_hallucinated_ids_are_ignored(self, cfg):
        memory = unit("Database", "We use PostgreSQL 16 for every tenant in the system.")
        memory.id = "mem_12345678"
        client = client_returning({"units": [{"id": "mem_99999999", "title": "Nonsense"}]})
        units, _, _ = optimize_with(client, [memory], cfg)
        assert units[0].title == "Database"

    @pytest.mark.parametrize(
        "payload",
        [
            "this is not json",
            {"wrong": "shape"},
            httpx.ConnectError("connection refused"),
            httpx.ReadTimeout("too slow"),
        ],
    )
    def test_every_failure_falls_back_quietly(self, cfg, payload):
        memory = unit("Database", "We use PostgreSQL 16 for every tenant in the system.")
        memory.id = "mem_12345678"
        units, _, warnings = optimize_with(client_returning(payload), [memory], cfg)
        assert units[0].title == "Database"  # deterministic result stands
        if not isinstance(payload, dict):
            assert warnings  # and the user is told

    def _near_duplicate_pair(self):
        a = unit(
            "Deploys",
            "Deploys go through GitHub Actions, which builds images and updates the "
            "ECS service definition for the environment.",
            source_file=Path("MEMORY.md"),
        )
        b = unit(
            "Deployment",
            "Deploys go through GitHub Actions, which builds images and updates the "
            "ECS service definitions for the environment.",
            source_file=Path("memory/deploy.md"),
        )
        a.id, b.id = "mem_aaaaaaaa", "mem_bbbbbbbb"
        return a, b

    def test_the_pair_really_is_a_near_duplicate(self, cfg):
        """Guard: without this, the veto test below would pass vacuously."""
        a, b = self._near_duplicate_pair()
        _, analysis = DeterministicOptimizer().optimize(
            [a, b], OptimizeContext(cfg=cfg, counter=HeuristicCounter())
        )
        assert len(analysis.near_dupes) == 1
        assert not b.is_live

    def test_merge_veto_restores_a_memory(self, cfg):
        a, b = self._near_duplicate_pair()
        vetoed_id = b.provenance

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            is_merge_pass = '"pairs"' in payload["messages"][1]["content"]
            content = (
                {"merges": [{"id": vetoed_id, "merge": False}]}
                if is_merge_pass
                else {"units": []}
            )
            return httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
            )

        client = LLMClient(llm_cfg(), transport=httpx.MockTransport(handler))
        units, analysis, warnings = optimize_with(client, [a, b], cfg)
        assert all(u.is_live for u in units), "the vetoed memory should be kept"
        assert analysis.near_dupes == []
        assert any("kept" in w for w in warnings)

    def test_merge_is_honoured_when_the_model_agrees(self, cfg):
        a, b = self._near_duplicate_pair()
        client = client_returning({"units": [], "merges": [{"id": b.provenance, "merge": True}]})
        units, analysis, _ = optimize_with(client, [a, b], cfg)
        assert not b.is_live
        assert len(analysis.near_dupes) == 1


class TestRunWithLLM:
    def test_requires_a_configured_model(self, runner, demo_project: Path):
        result = runner.invoke(app, ["run", "--llm"])
        assert result.exit_code == 1
        assert "llm.model" in result.output

    def test_a_dead_endpoint_still_completes(self, runner, demo_project: Path, monkeypatch):
        config = demo_project / ".memdex" / "config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                "llm:\n  enabled: false",
                "llm:\n  enabled: true\n  model: qwen2.5:14b\n  base_url: http://127.0.0.1:9/v1\n"
                "  timeout: 1",
            ),
            encoding="utf-8",
        )
        result = runner.invoke(app, ["run", "--llm"])
        assert result.exit_code == 0, result.output
        assert list(demo_project.glob("memory/*/*.md"))
        assert "Keeping the deterministic result." in flat(result.output)


class TestBootstrap:
    def test_requires_an_llm(self, runner, bare_project: Path):
        result = runner.invoke(app, ["bootstrap"])
        assert result.exit_code == 1
        assert "large context window" in result.output

    def test_run_explains_bootstrap_when_there_is_no_memory(self, runner, bare_project: Path):
        result = runner.invoke(app, ["run"])
        assert result.exit_code == 0
        assert "No project memory found" in result.output
        assert "memdex bootstrap" in result.output

    def test_generates_and_writes_memories(self, bare_project: Path):
        cfg = load_config(bare_project)
        cfg.llm = llm_cfg()
        client = client_returning(
            {
                "memories": [
                    {
                        "title": "Service layout",
                        "category": "architecture",
                        "body": "Three services talk over a queue.",
                        "importance": 0.8,
                    },
                    {
                        "title": "Testing",
                        "category": "development",
                        "body": "Run make test before every commit.",
                        "importance": 0.6,
                    },
                    {"title": "", "body": "dropped: no title"},
                ]
            }
        )
        written = generate_seed_memory(cfg, dry_run=False, client=client)
        assert written == 2
        assert (bare_project / "memory" / "architecture" / "service-layout.md").is_file()
        assert (bare_project / "memory" / "development" / "testing.md").is_file()

    def test_dry_run_writes_nothing(self, bare_project: Path):
        cfg = load_config(bare_project)
        cfg.llm = llm_cfg()
        client = client_returning(
            {"memories": [{"title": "A", "category": "general", "body": "b", "importance": 0.5}]}
        )
        generate_seed_memory(cfg, dry_run=True, client=client)
        assert not (bare_project / "memory").exists()

    def test_unusable_output_is_an_error_not_an_empty_tree(self, bare_project: Path):
        cfg = load_config(bare_project)
        cfg.llm = llm_cfg()
        with pytest.raises(MemdexError, match="usable memories"):
            generate_seed_memory(cfg, dry_run=False, client=client_returning({"memories": []}))

    def test_bootstrapped_memory_then_runs_normally(self, runner, bare_project: Path):
        cfg = load_config(bare_project)
        cfg.llm = llm_cfg()
        generate_seed_memory(
            cfg,
            dry_run=False,
            client=client_returning(
                {
                    "memories": [
                        {
                            "title": "Service layout",
                            "category": "architecture",
                            "body": "Three services talk over a queue, never directly.",
                            "importance": 0.8,
                        }
                    ]
                }
            ),
        )
        result = runner.invoke(app, ["run"])
        assert result.exit_code == 0, result.output
        assert "Service layout" in (bare_project / "MEMORY.md").read_text(encoding="utf-8")

    def test_validation_rejects_junk(self):
        kept = validate_memories(
            {
                "memories": [
                    {"title": "Good", "body": "text", "category": "decisions", "importance": 0.7},
                    {"title": "No body"},
                    "not an object",
                    {"title": "Bad category", "body": "t", "category": "nope", "importance": "x"},
                ]
            }
        )
        assert [m["title"] for m in kept] == ["Good", "Bad category"]
        assert kept[1]["category"] == "general"
        assert kept[1]["importance"] == 0.6
