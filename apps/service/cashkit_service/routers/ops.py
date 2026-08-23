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
from fastapi import APIRouter, Request, Response

from ..deps import ClockDep, DatabaseDep

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


@router.get("/metrics")
async def metrics(request: Request, database: DatabaseDep, clock: ClockDep) -> Response:
    """The Prometheus exposition, or 404 when metrics are switched off.

    The derived gauges are recomputed here rather than on a timer: a scrape is
    the only moment the numbers are read, and a background refresh would keep a
    connection busy on a schedule nobody is watching.

    Never published — `ops/Caddyfile` answers 404 for this path, and only the
    metrics agent on the compose network reaches it.
    """
    from ..metrics import MetricsRegistry, refresh_db_gauges, render

    registry: MetricsRegistry | None = getattr(request.app.state, "metrics", None)
    if registry is None:
        return Response(status_code=404)
    settings = request.app.state.settings
    try:
        async with database.connect() as conn:
            await refresh_db_gauges(
                registry, conn, now=clock.now(), backup_marker=settings.backup_success_file
            )
    except Exception:  # noqa: BLE001
        # Serve what is in process rather than nothing. A scrape that fails
        # because the database is busy takes the request metrics down with it,
        # and those are exactly what an operator wants during a database
        # problem. The uptime and health alarms cover the database itself.
        pass
    return Response(content=render(registry), media_type="text/plain; version=0.0.4")
