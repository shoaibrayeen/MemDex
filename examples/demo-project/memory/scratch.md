The staging environment mirrors production but runs a single replica of each
service. Its database is restored from a nightly production snapshot with all
customer PII scrubbed by `scripts/scrub.sql`.

Staging credentials live in 1Password under "Phoenix — staging". Never point a
local dev environment at staging; use `make dev` instead.

Here is a markdown heading that must not split this file, because it lives
inside a code fence:

```markdown
# Not A Real Heading

## Also Not A Heading
```

That fence is the reason this file stays a single memory.
