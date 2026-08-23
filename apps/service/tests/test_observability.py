"""Sentry, and the payload that must never reach it (SPEC §9, §11).

SPEC §9 lists Sentry as a subprocessor for **error tracking**. It is not a
second copy of the user's financial data, and the 30-day payload retention of
§4 would be meaningless if a stack frame carried the same bytes to a different
vendor on a different schedule. So an exception raised inside a turn — with the
user's sentence, the book snapshot and the model request all in scope — must
arrive with the shape and none of the content.

The tests below drive the real `before_send` with the shape of a real Sentry
event, because "we set `include_local_variables=False`" is a claim about a
setting and a setting is one upgrade away from a new default.
"""

from __future__ import annotations

import pytest

from cashkit_service.config import Settings
from cashkit_service.observability import SCRUB_KEYS, install_sentry, scrub

SECRET = "my salary is 2617.33 and my rent is 912.50"


def test_no_dsn_installs_nothing():
    """The state every test and every development run is in."""
    assert install_sentry(Settings(database_url="unused://", sentry_dsn="")) is False


def test_a_local_variable_carrying_the_user_sentence_is_removed():
    """The likeliest leak: a frame's locals.

    An exception inside the turn pipeline has `text`, `snapshot` and `messages`
    on the stack. Sentry captures frame variables by default.
    """
    event = {
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "boom",
                    "stacktrace": {
                        "frames": [
                            {
                                "function": "run_turn",
                                "vars": {"text": SECRET, "user_id": "6f1c…"},
                            }
                        ]
                    },
                }
            ]
        }
    }
    cleaned = scrub(event)
    assert "vars" not in cleaned["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert SECRET not in repr(cleaned)


def test_the_request_body_never_travels():
    event = {"request": {"data": {"text": SECRET}, "url": "https://app/turns"}}
    cleaned = scrub(event)
    assert "request" not in cleaned
    assert SECRET not in repr(cleaned)


@pytest.mark.parametrize("key", sorted(SCRUB_KEYS))
def test_every_declared_key_is_redacted_wherever_it_is_found(key: str):
    """The walk is over the whole event, not over known locations.

    The interesting fields move between SDK versions, and a list of locations
    is a list that goes stale in silence.
    """
    event = {"extra": {"deeply": {"nested": [{key: SECRET}]}}}
    cleaned = scrub(event)
    assert SECRET not in repr(cleaned)
    assert "scrubbed" in repr(cleaned)


def test_the_shape_survives_so_the_report_is_still_useful():
    """Scrubbing is not deleting: an unusable report is not a safer report."""
    event = {
        "exception": {"values": [{"type": "ZeroDivisionError", "value": "division by zero"}]},
        "tags": {"request_id": "abc123", "environment": "production"},
        "extra": {"turn_id": "7f2e", "input_text": SECRET},
    }
    cleaned = scrub(event)
    assert cleaned["exception"]["values"][0]["type"] == "ZeroDivisionError"
    assert cleaned["tags"]["request_id"] == "abc123", (
        "the request_id is the whole point (SPEC §11) and must survive"
    )
    assert cleaned["extra"]["turn_id"] == "7f2e", "the chain's second link survives too"
    assert SECRET not in repr(cleaned)


def test_the_client_is_configured_before_the_scrub_is_needed():
    """Defence in depth, asserted on the real client's options.

    `before_send` is the last line; these three are the first, and each of them
    is a default the SDK is entitled to change.
    """
    settings = Settings(
        database_url="unused://",
        # A syntactically valid DSN pointing nowhere. `sentry_sdk.init` does not
        # connect, so nothing leaves this process.
        sentry_dsn="https://public@localhost/1",
        environment="test",
    )
    assert install_sentry(settings) is True
    import sentry_sdk

    options = sentry_sdk.get_client().options
    assert options["send_default_pii"] is False
    assert options["include_local_variables"] is False
    assert options["max_request_body_size"] == "never"
    assert options["traces_sample_rate"] == 0.0
    assert options["before_send"] is scrub
    sentry_sdk.get_client().close()


async def test_the_request_id_reaches_the_response_and_could_reach_sentry(auth_client):
    """The chain's first link, end to end through the middleware."""
    response = await auth_client.get("/me", headers={"x-request-id": "chain-check"})
    assert response.headers["x-request-id"] == "chain-check"
