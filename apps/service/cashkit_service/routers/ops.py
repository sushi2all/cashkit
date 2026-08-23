"""Operational endpoints (SPEC §11, §12).

They are not part of the product API and are `include_in_schema=False`, so
`apps/service/openapi.json` stays the SPEC §3 contract and the generated
TypeScript client gains nothing it would never call — a health probe in the
client's type surface is a path someone eventually calls from a screen.

`/healthz` answers what a load balancer actually needs to know, which is not
"is the process alive" but "can this instance serve a request". A book read
needs Postgres, so the check touches Postgres. Reporting healthy while the
database is unreachable would make Caddy send traffic to an instance that can
only 500.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Response

from ..deps import DatabaseDep

router = APIRouter(tags=["ops"], include_in_schema=False)


@router.get("/healthz")
async def healthz(database: DatabaseDep) -> Response:
    """Readiness: the process is up *and* the database answers."""
    try:
        async with database.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any failure to reach Postgres is unready
        return Response(
            content='{"status":"unready","database":"unreachable"}',
            media_type="application/json",
            status_code=503,
        )
    return Response(
        content='{"status":"ok"}', media_type="application/json", status_code=200
    )
