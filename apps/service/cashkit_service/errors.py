"""HTTP failures, and the one rule about diagnostics.

Engine diagnostics are never HTTP errors. A diagnostic is an answer: it has a
code, a severity, a message and a suggested fix, and it travels verbatim inside
a 200 payload (ADR-0015, SPEC §3). Only service-level failures — no session, no
book, an expired proposal — become status codes.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


class ServiceError(HTTPException):
    """A service-level failure, with a stable machine-readable code."""

    def __init__(self, status_code: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message, **extra})
        self.code = code


def unauthorized(message: str = "No valid session.") -> ServiceError:
    return ServiceError(status.HTTP_401_UNAUTHORIZED, "AUTH_REQUIRED", message)


def invalid_link(message: str) -> ServiceError:
    return ServiceError(status.HTTP_400_BAD_REQUEST, "LINK_INVALID", message)


def no_book(message: str = "This account has no book yet.") -> ServiceError:
    return ServiceError(status.HTTP_404_NOT_FOUND, "NO_BOOK", message)


def book_exists(message: str = "This account already has a book.") -> ServiceError:
    return ServiceError(status.HTTP_409_CONFLICT, "BOOK_EXISTS", message)


def not_found(code: str, message: str) -> ServiceError:
    return ServiceError(status.HTTP_404_NOT_FOUND, code, message)


def conflict(code: str, message: str, **extra: Any) -> ServiceError:
    return ServiceError(status.HTTP_409_CONFLICT, code, message, **extra)


def bad_request(code: str, message: str, **extra: Any) -> ServiceError:
    return ServiceError(status.HTTP_400_BAD_REQUEST, code, message, **extra)


def busy(message: str = "The book is busy; retry.") -> ServiceError:
    return ServiceError(status.HTTP_503_SERVICE_UNAVAILABLE, "BOOK_BUSY", message)
