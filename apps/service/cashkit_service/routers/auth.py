"""Magic-link endpoints (SPEC §3)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, EmailStr, Field

from ..auth import consume_link_token, issue_link_token, normalize_email, open_session
from ..config import Settings
from ..deps import ClockDep, ConnDep, MailerDep, SettingsDep
from ..mail import MagicLink

router = APIRouter(tags=["auth"])


class LinkRequest(BaseModel):
    email: EmailStr
    # The client tells the service which link shape to send. Web gets an https
    # URL; a development build gets the custom scheme (SPEC §3 — universal
    # links wait for the Apple enrolment).
    platform: Literal["web", "mobile"] = "web"


class VerifyRequest(BaseModel):
    token: str = Field(min_length=1)
    platform: Literal["web", "mobile"] = "web"


class Session(BaseModel):
    token: str
    expires_at: str
    platform: Literal["web", "mobile"]


def _link_url(token: str, platform: str, settings: Settings) -> str:
    """Where the mailed link points.

    Both shapes land on the same route in the client's router, so the token
    travels the same way whichever platform asked for it. Neither host is
    hard-coded any more: the web origin and the mobile scheme are
    configuration, because they differ per environment and the development
    build's scheme is not the production app's (S1 handoff §5, SPEC §3).
    """
    path = settings.verify_path
    if platform == "mobile":
        return f"{settings.mobile_scheme}://{path.lstrip('/')}?token={token}"
    return f"{settings.web_app_url.rstrip('/')}{path}?token={token}"


@router.post("/auth/link", status_code=status.HTTP_202_ACCEPTED)
async def request_link(
    body: LinkRequest, conn: ConnDep, clock: ClockDep, settings: SettingsDep, mailer: MailerDep
) -> Response:
    """Send a magic link.

    The answer is the same whether or not the address has an account: an
    endpoint that distinguishes them is an account-enumeration oracle.
    """
    email = normalize_email(str(body.email))
    token = await issue_link_token(conn, email=email, clock=clock, settings=settings)
    await mailer.send_magic_link(
        MagicLink(email=email, token=token, url=_link_url(token, body.platform, settings))
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/auth/verify")
async def verify_link(
    body: VerifyRequest, conn: ConnDep, clock: ClockDep, settings: SettingsDep
) -> Session:
    """Exchange a single-use link token for a bearer session."""
    from ..auth import upsert_user

    email = await consume_link_token(conn, token=body.token, clock=clock)
    user_id = await upsert_user(conn, email=email, clock=clock)
    token, expires_at = await open_session(
        conn, user_id=user_id, platform=body.platform, clock=clock, settings=settings
    )
    return Session(token=token, expires_at=expires_at.isoformat(), platform=body.platform)
