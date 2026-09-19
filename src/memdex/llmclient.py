"""One HTTP client for every supported LLM provider.

Ollama, LM Studio and the various proxies all speak the OpenAI chat-completions
shape, so Memdex speaks only that and lets ``base_url`` pick the backend. Every
failure mode — unreachable, slow, chatty, invalid JSON — raises ``LLMError``,
because the callers are all built to carry on without the model.
"""

from __future__ import annotations

import json
import re

from memdex.config import LLMConfig
from memdex.errors import LLMError

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMClient:
    def __init__(self, cfg: LLMConfig, transport=None) -> None:  # noqa: ANN001 - httpx type
        self.cfg = cfg
        self._transport = transport  # tests inject httpx.MockTransport here

    @property
    def base_url(self) -> str:
        base = self.cfg.resolved_base_url
        if not base:
            raise LLMError(
                f"No base URL configured for provider {self.cfg.provider!r}.",
                hint="Set llm.base_url in .memdex/config.yaml.",
            )
        return base

    def _headers(self) -> dict[str, str]:
        """The key comes from the environment at call time and is never logged."""
        key = self.cfg.resolved_api_key
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _client(self):
        import httpx

        kwargs = {"timeout": self.cfg.timeout, "headers": self._headers()}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def chat_json(self, system: str, user: str) -> dict:
        """Ask for a JSON object and return it parsed."""
        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        try:
            with self._client() as client:
                response = client.post(f"{self.base_url}/chat/completions", json=payload)
                response.raise_for_status()
                body = response.json()
        except Exception as exc:  # noqa: BLE001
            raise LLMError(
                f"Could not reach the LLM at {self.base_url} ({exc.__class__.__name__}).",
                hint="Check that the server is running and llm.model exists.",
            ) from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("The LLM returned an unexpected response shape.") from exc

        return parse_json_object(content)

    def ping(self) -> tuple[bool, str]:
        """Cheap reachability check for `memdex doctor`."""
        import httpx

        try:
            kwargs = {"timeout": min(self.cfg.timeout, 5.0), "headers": self._headers()}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            with httpx.Client(**kwargs) as client:
                response = client.get(f"{self.base_url}/models")
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            return False, f"{self.base_url} unreachable ({exc.__class__.__name__})"
        return True, f"{self.cfg.provider}:{self.cfg.model} at {self.base_url}"


def parse_json_object(content: str) -> dict:
    """Parse a JSON object, tolerating the code fences small models like to add."""
    if not isinstance(content, str):
        raise LLMError("The LLM returned a non-text response.")
    text = content.strip()
    match = FENCE_RE.search(text)
    if match:
        text = match.group(1).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("The LLM did not return JSON.")
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError("The LLM returned malformed JSON.") from exc
    if not isinstance(data, dict):
        raise LLMError("The LLM returned JSON that was not an object.")
    return data
