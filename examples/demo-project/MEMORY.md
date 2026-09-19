# Project Memory

Sure, here's what I know about this project so far!

## Architecture

### Backend services

The backend is three Go services behind an nginx gateway: `api`, `worker` and
`scheduler`. They communicate over NATS subjects, never by calling each other
directly, so that a slow consumer can never stall a request path.

The `api` service owns all HTTP handling. The `worker` service owns long jobs.
The `scheduler` service owns cron-like triggers and nothing else.

### Database

We use PostgreSQL 16 as the primary store. Every table carries a `tenant_id`
and row-level security is enabled, so a missing tenant filter fails closed
instead of leaking across tenants.

Migrations live in `db/migrations` and run with `goose`. Never edit an applied
migration — add a new one.

### Vector search

We use Chroma for semantic search over support tickets. It runs locally in
development and as a sidecar in production.

Hope this helps!

## Decisions

### Chroma selection

We chose Chroma over Elasticsearch for ticket similarity search.

Elasticsearch would have meant running a JVM cluster for one feature. Chroma
embeds in the same process in development, persists to a local directory, and
the whole index rebuilds in under a minute, so the operational cost is close to
zero. The tradeoff we accepted is weaker filtering than Elasticsearch offers.

### Event sequencing

Events carry a monotonic `(tenant_id, seq)` pair assigned by Postgres. Consumers
deduplicate on that pair, which means at-least-once delivery from NATS is safe
and we never needed exactly-once semantics.

### Session storage (deprecated)

We used to keep sessions in Redis with a 30 minute TTL. This is deprecated — it
was replaced by stateless JWTs in March and the Redis instance is gone.

## Development

### Testing

Run `make test` for unit tests and `make test-integration` for the docker-compose
suite. Integration tests need Postgres on port 5433.

Let me know if you need anything else!

Coverage must stay above 80% — CI fails below it.

### Local setup

Copy `.env.example` to `.env`, then run `make dev`. The gateway comes up on
port 8080 and the admin UI on 8081.

## Conventions

### Error handling

Always wrap errors with `fmt.Errorf("doing x: %w", err)`. Never return a bare
error from a handler — the middleware needs the wrapped context to map it to a
status code.

### Naming

Package names are singular (`user`, not `users`). Test files end in `_test.go`.
Exported identifiers always carry a doc comment.

## Projects

### Phoenix

Phoenix is the extraction pipeline that replaces the legacy ETL. It reads from
the events table, normalizes payloads with a schema registry, and writes to the
warehouse in hourly partitions.

Phase 1 (read path) shipped. Phase 2 (backfill) is in progress.

## Database

We use PostgreSQL 16 as the primary store. Every table carries a `tenant_id`
and row-level security is enabled, so a missing tenant filter fails closed
instead of leaking across tenants.

Migrations live in `db/migrations` and run with `goose`. Never edit an applied
migration — add a new one.

## Notes on the deployment pipeline

Okay, here's what I remember.

Deploys go through GitHub Actions. A merge to `main` builds images, pushes them
to ECR, and updates the ECS service definition. Rollback is `make rollback`,
which re-points the service at the previous task definition.

Secrets come from AWS Secrets Manager and are injected as environment variables
at task start. Nothing reads a secret from disk.

```bash
# the only deploy command anyone should need
make deploy ENV=production
```

Deploys go through GitHub Actions. A merge to `main` builds images, pushes them
to ECR, and updates the ECS service definition. Rollback is `make rollback`,
which re-points the service at the previous task definition.
