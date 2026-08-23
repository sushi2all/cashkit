"""The structured request log (SPEC §11's third layer).

Two properties, and only one of them is about the log being useful.

The useful half: one JSON line per request, carrying the ``request_id`` that
the turn journal already puts on ``turns`` and ``llm_calls``, so the SPEC §11
chain ``request_id → turn_id → llm_calls.seq → proposal_id`` is walkable from
an access log line.

The half that is a compliance property: **the line names nobody.** SPEC §11's
hard rule is written about metric labels, and a log line is the easier place to
leak an identifier — a raw path carries the ids the route template hides. So
the route is asserted to be the template, and every line the whole suite emits
is checked against the field list.
"""

from __future__ import annotations

import json
import logging

import pytest

from cashkit_service.requestlog import (
    FIELDS,
    LOGGER_NAME,
    assert_content_free,
    install_file_handler,
)


@pytest.fixture
def lines(caplog):
    """Every request-log line emitted while the test runs, parsed."""
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    yield caplog
    # nothing to tear down: caplog owns the handler


def parsed(caplog) -> list[dict]:
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == LOGGER_NAME
    ]


async def test_one_line_per_request_with_the_ninety_percent_fields(auth_client, lines):
    await auth_client.get("/me", headers={"x-request-id": "chain-me"})
    entries = parsed(lines)
    assert len(entries) == 1
    line = entries[0]
    assert set(line) == set(FIELDS)
    assert line["request_id"] == "chain-me"
    assert line["method"] == "GET"
    assert line["route"] == "/me"
    assert line["status"] == 200
    assert isinstance(line["duration_ms"], int)


async def test_the_route_is_the_template_not_the_path(book_client, lines):
    """`/proposals/{proposal_id}`, never `/proposals/6f1c…`.

    A raw path is an identifier with a slash in front of it. It would also make
    the per-endpoint latency series unaggregatable, which is the other half of
    why SPEC §11 wants templates.
    """
    created = await book_client.post(
        "/book/edits",
        json={"origin": "settings", "ops": [{"op": "set_opening_balance", "amount": "10.00"}]},
    )
    proposal_id = created.json()["proposal"]["id"]
    lines.clear()
    await book_client.post(f"/proposals/{proposal_id}", json={"action": "discard"})

    routes = [line["route"] for line in parsed(lines)]
    assert routes == ["/proposals/{proposal_id}"]
    assert proposal_id not in json.dumps(parsed(lines))


async def test_an_unmatched_path_does_not_become_a_label(auth_client, lines):
    """A 404 on an arbitrary URL must not put that URL in the log.

    Otherwise a scanner walking `/wp-admin/…` writes its own labels into the
    observability layer, and any of them could carry a token from a mistyped
    link.
    """
    await auth_client.get("/definitely-not-a-route/abc123")
    assert [line["route"] for line in parsed(lines)] == ["<unmatched>"]


async def test_a_failing_request_still_produces_a_line(auth_client, lines):
    """A 4xx or 5xx is the request an operator most wants to see."""
    await auth_client.get("/book/state")  # no book: 404
    entries = parsed(lines)
    assert entries and entries[-1]["status"] == 404


async def test_no_line_the_suite_emits_carries_an_identifier(book_client, lines):
    """The content-free rule, checked over a realistic walk of the API.

    Auth, a book read, a proposal and its confirmation — the paths that carry
    an email, a bearer token, a book id and a proposal id between them.
    """
    created = await book_client.post(
        "/book/edits",
        json={"origin": "settings", "ops": [{"op": "set_opening_balance", "amount": "42.00"}]},
    )
    await book_client.post(
        f"/proposals/{created.json()['proposal']['id']}", json={"action": "accept"}
    )
    await book_client.get("/book/state")
    await book_client.get("/me")

    entries = parsed(lines)
    assert len(entries) >= 4
    for line in entries:
        assert_content_free(line)


def test_assert_content_free_rejects_what_it_claims_to():
    """The guard's own test: a rule that matches nothing passes everything."""
    good = {"ts": "t", "request_id": "r", "method": "GET", "route": "/me", "status": 200, "duration_ms": 1}
    assert_content_free(good)

    with pytest.raises(AssertionError, match="outside"):
        assert_content_free({**good, "user_id": "u"})
    with pytest.raises(AssertionError, match="email"):
        assert_content_free({**good, "route": "/users/by-email/a@b.c"})
    with pytest.raises(AssertionError, match="token"):
        assert_content_free({**good, "route": "/auth/verify?token=abc"})


def test_the_file_handler_rotates_on_the_retention_policy(tmp_path):
    """Rotation and retention are one number, so they cannot disagree.

    `backupCount` is the retention in days, passed from the same setting the
    purge job reads. Widening the policy and widening the rotation is one edit.
    """
    handler = install_file_handler(tmp_path / "logs", backup_count=90)
    try:
        logging.getLogger(LOGGER_NAME).info('{"ts":"t"}')
        handler.flush()
        assert handler.backupCount == 90
        assert handler.suffix == "%Y-%m-%d"
        written = (tmp_path / "logs" / "request.log").read_text()
        assert written.strip() == '{"ts":"t"}', "the line is the record; no level, no logger name"
    finally:
        logging.getLogger(LOGGER_NAME).removeHandler(handler)
        logging.getLogger(LOGGER_NAME).propagate = True
        handler.close()
