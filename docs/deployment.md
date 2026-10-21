# Deployment runbook — AWS EC2 (prepared, not deployed)

This is a portfolio-scale single-EC2 deployment: Internet → Nginx → FastAPI container → private
PostgreSQL, Redis, and Qdrant containers. It is not a high-availability enterprise architecture.

## Prerequisites and network

Create an EC2 instance, restrict SSH (22) to a known source, and expose only 80 and 443. Do **not**
open 5432, 6379, or 6333. Install Docker Engine plus the Docker Compose plugin, then clone
`https://github.com/Haroldojo/RAG-for-DNS-lookup.git`.

Create a server-only `.env` from `.env.example`, set `APP_ENV=production`, `DEBUG=false`, a strong
`POSTGRES_PASSWORD`, `GROQ_API_KEY` if generation is required, and any managed-service URLs. Never
commit this file. Future AWS/EC2/SSH/Groq credentials belong in GitHub Actions Secrets, not source,
Dockerfiles, workflow YAML, or documentation.

## Safe startup order

1. `docker compose up -d postgres redis qdrant`
2. Wait for `docker compose ps` to show healthy dependencies.
3. Run migrations once from an operator job: `docker compose run --rm app alembic upgrade head`.
4. Ingest documents: `docker compose run --rm app python -m app.rag.bootstrap`.
5. `docker compose up -d app nginx`
6. Run `SMOKE_BASE_URL=http://127.0.0.1 python scripts/smoke_test.py` on the host or an equivalent
   network-reachable location.
7. Inspect `docker compose logs --tail=200 app` and `/health` plus `/ready`.

Only one deployment job should run Alembic migrations. Competing replicas can race schema changes.
For rollback, deploy the prior image/code revision, then use `alembic downgrade <revision>` only when
the migration has a reviewed safe downgrade; database backup/restore is safer for destructive changes.

## Operations

`/health` is container/process liveness; `/ready` reports configured dependency state. Restart policies
restart a failed container. PostgreSQL uses a named volume, so conversation/audit history survives an app
restart. Redis/Qdrant restarts degrade or temporarily remove cache/retrieval availability according to
the existing fail-open/fail-safe application policies.

Use configurable `WEB_CONCURRENCY`; do not use `--reload` in production. Worker count, CPU/RAM, and
timeout values need measurement: local defaults are not capacity claims. Vertical scaling increases one
machine's resources; horizontal scaling adds replicas and requires migration coordination and shared
external state.
