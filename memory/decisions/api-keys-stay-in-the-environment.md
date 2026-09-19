---
memdex:
  id: mem_9a4c2f1e
  title: API keys stay in the environment
  category: decisions
  description: config.yaml names the variable that holds a key; a pasted key is rejected at load.
  importance: 0.85
  source: development session — OpenAI provider
---
# API keys stay in the environment

`llm.api_key_env` holds the *name* of the environment variable (default
`OPENAI_API_KEY` for `provider: openai`, `MEMDEX_LLM_API_KEY` otherwise); the key
itself is read at call time and sent only as the request's Authorization header.

A value that looks like a real key pasted into `config.yaml` is rejected at load
with a `ConfigError` — `.memdex/config.yaml` is deliberately committable, and a
committable file must never be one paste away from leaking a secret. Error
messages and doctor output name the variable, never its value.

Because a remote endpoint means memory content leaves the machine, every LLM
command prints a warning first when `llm.is_remote` is true (host not in
`LOCAL_HOSTS`).
