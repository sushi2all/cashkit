"""Account endpoints: profile, deletion, data export (SPEC §3, §9)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Response, status

from ..auth import revoke_all_sessions
from ..db import books, import_jobs, llm_calls, proposals, sessions, turns, users
from ..deps import ClockDep, ConnDep, PrincipalDep, get_books, load_book_row
from fastapi import Request
from pydantic import BaseModel

router = APIRouter(tags=["account"])


class Me(BaseModel):
    user_id: str
    email: str
    created_at: str
    has_book: bool
    book_id: str | None = None
    active_scenario: str | None = None


@router.get("/me")
async def get_me(conn: ConnDep, principal: PrincipalDep) -> Me:
    row = (
        await conn.execute(sa.select(users.c.created_at).where(users.c.id == principal.user_id))
    ).one()
    book = await load_book_row(conn, principal.user_id)
    return Me(
        user_id=str(principal.user_id),
        email=principal.email,
        created_at=row.created_at.isoformat(),
        has_book=book is not None,
        book_id=str(book.id) if book else None,
        active_scenario=book.active_scenario if book else None,
    )


def _jsonable(value: Any) -> Any:
    """The export encoder: Decimal and date become strings, never floats."""
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


async def _rows(conn, stmt) -> list[dict[str, Any]]:
    result = await conn.execute(stmt)
    return [_jsonable(dict(r._mapping)) for r in result]


@router.get(
    "/me/export",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}, "description": "Everything the account owns."}},
)
async def export_me(request: Request, conn: ConnDep, principal: PrincipalDep) -> Response:
    """Everything the user owns, one archive (GDPR, SPEC §3/§9).

    Postgres rows plus the book directory itself — the YAML revisions and the
    ledger are the user's data, not an internal format they are locked out of.
    Session and link token hashes are excluded: they are credentials, and
    exporting them would widen the blast radius of a leaked archive.
    """
    book = await load_book_row(conn, principal.user_id)
    payload: dict[str, Any] = {
        "user": (await _rows(conn, sa.select(users).where(users.c.id == principal.user_id)))[0],
        "sessions": await _rows(
            conn,
            sa.select(
                sessions.c.id, sessions.c.platform, sessions.c.expires_at,
                sessions.c.created_at, sessions.c.last_seen_at,
            ).where(sessions.c.user_id == principal.user_id),
        ),
        "books": await _rows(conn, sa.select(books).where(books.c.user_id == principal.user_id)),
        "turns": await _rows(conn, sa.select(turns).where(turns.c.user_id == principal.user_id)),
    }
    if book is not None:
        payload["llm_calls"] = await _rows(
            conn,
            sa.select(llm_calls).where(
                llm_calls.c.turn_id.in_(sa.select(turns.c.id).where(turns.c.user_id == principal.user_id))
            ),
        )
        payload["proposals"] = await _rows(conn, sa.select(proposals).where(proposals.c.book_id == book.id))
        payload["import_jobs"] = await _rows(conn, sa.select(import_jobs).where(import_jobs.c.book_id == book.id))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, rows in payload.items():
            zf.writestr(f"account/{name}.json", json.dumps(rows, indent=2, sort_keys=True))
        if book is not None:
            root = Path(book.storage_path)
            for path in sorted(root.rglob("*")):
                if path.is_file() and ".cashkit/lock" not in path.as_posix():
                    zf.write(path, f"book/{path.relative_to(root).as_posix()}")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=cashkit-export.zip"},
    )


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(request: Request, conn: ConnDep, principal: PrincipalDep, clock: ClockDep) -> Response:
    """Full account deletion (SPEC §9).

    Every session is revoked, every Postgres row for the account is deleted —
    turns and llm_calls included, by cascade — and the book directory is
    removed from the volume. Backups are S6's 30-day window.
    """
    book = await load_book_row(conn, principal.user_id)
    runtime = get_books(request)
    await revoke_all_sessions(conn, user_id=principal.user_id)
    if book is not None:
        await runtime.forget(book.id, delete_storage=True)
    # users is the root of every cascade in the schema.
    await conn.execute(users.delete().where(users.c.id == principal.user_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
