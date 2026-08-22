"""Magic-link delivery.

The link token leaves the service through the mailer and nowhere else. No
endpoint ever returns it, in any mode — a debug flag that puts a login token in
an HTTP response would defeat the whole flow, and this service has no such
mode. Tests install :class:`CapturingMailer` and read the link from there.

The real provider is S6's (SPEC §12 secrets, §9 subprocessor list).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class MagicLink:
    email: str
    token: str
    url: str


class Mailer(Protocol):
    async def send_magic_link(self, link: MagicLink) -> None: ...


class ConsoleMailer:
    """Development delivery: the link goes to the process log."""

    async def send_magic_link(self, link: MagicLink) -> None:
        print(f"[magic-link] {link.email} -> {link.url}")


@dataclass
class CapturingMailer:
    """Test delivery: the link is kept in memory."""

    sent: list[MagicLink] = field(default_factory=list)

    async def send_magic_link(self, link: MagicLink) -> None:
        self.sent.append(link)

    def last_for(self, email: str) -> MagicLink:
        for link in reversed(self.sent):
            if link.email == email.strip().lower():
                return link
        raise AssertionError(f"no magic link was sent to {email}")
