# cashkit-service

The CashKit MLP consumer service (`SPEC-mlp-consumer.md`): a FastAPI application
that wraps the `cashkit` SDK in process, one book directory per user.

Session S1 built the deterministic core — no model call anywhere in this package.

## Run the tests

```bash
uv sync --all-packages
docker compose -f apps/service/docker-compose.dev.yml up -d --wait
uv run pytest apps/service/tests -q
```

The test fixture starts the Postgres container itself when it is not already up,
so the compose command above is optional.

## Run the service

```bash
export CASHKIT_DATABASE_URL=postgresql+asyncpg://cashkit:cashkit@localhost:55432/cashkit
export CASHKIT_BOOKS_ROOT=/var/lib/cashkit/books
uv run python -m cashkit_service.migrate
uv run uvicorn cashkit_service.app:app --port 8000
```
