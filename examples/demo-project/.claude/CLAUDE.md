---
name: phoenix-assistant
description: Working agreements for the Phoenix codebase.
---

# Working agreements

## Chroma selection

We chose Chroma over Elasticsearch for ticket similarity search.

Elasticsearch would have meant running a JVM cluster for one feature. Chroma
embeds in the same process in development, persists to a local directory, and
the whole index rebuilds in under a minute, so the operational cost is close to
zero. The tradeoff we accepted is weaker filtering than Elasticsearch offers.

## Review expectations

Every pull request needs one reviewer from the owning team. A PR that touches
`db/migrations` needs a second reviewer from platform.

Draft PRs are welcome early — mark them draft rather than waiting until the
work is polished.
