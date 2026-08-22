"""Publish the OpenAPI schema.

    uv run python -m cashkit_service.openapi

The schema is the contract between this service and ``apps/client``: S3
generates the TypeScript client from it, and nobody hand-writes either side.
It is committed so a drift check has something to compare against, and a test
asserts the committed copy matches what the app produces.
"""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def build() -> dict:
    """The schema, built from a service with no environment attached.

    Settings are constructed explicitly rather than read from the environment,
    so the published schema is the same on every machine.
    """
    from .app import create_app
    from .config import Settings

    # A well-formed URL that is never connected to: building a schema must not
    # need a database, and must not depend on one being reachable.
    app = create_app(
        settings=Settings(
            database_url="postgresql+asyncpg://schema:schema@localhost:1/schema",
            books_root=Path("/tmp/cashkit-schema"),
        )
    )
    return app.openapi()


def render() -> str:
    return json.dumps(build(), indent=2, sort_keys=True) + "\n"


def write(path: Path = SCHEMA_PATH) -> Path:
    path.write_text(render())
    return path


if __name__ == "__main__":
    print(f"wrote {write()}")
