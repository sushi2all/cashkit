"""The structural guard (ADR-0029), tested on its own.

ADR-0029 says enforcement is *structural and post-interpretation*, because a
prompt rule was demonstrably not enough. These tests exercise the sorting
function directly, with no model and no book: whatever a model emits, the guard
decides what class it belongs to, and the classes decide what may happen.
"""

from __future__ import annotations

from cashkit_service.agent.guard import MODEL_OPS, READ_OPS, guard
from cashkit_service.intents.read import READ_INTENTS
from cashkit_service.ops.schema import HOST_OPS, MUTATION_INTENTS


# --- the surface itself ---------------------------------------------------- #


def test_the_model_surface_is_the_21_intents_plus_one_read_tool():
    """SPEC §2.3: R1–R12 plus the single host tool ``query_ledger``."""
    assert READ_OPS == frozenset(READ_INTENTS) | {"query_ledger"}
    assert MODEL_OPS == READ_OPS | MUTATION_INTENTS
    assert len(MODEL_OPS) == 12 + 1 + 9


def test_no_host_operation_is_inside_the_model_surface():
    """SPEC §2.5, D-MLP-03: host ops exist only on the interface→service path."""
    assert MODEL_OPS & HOST_OPS == frozenset()


# --- sorting --------------------------------------------------------------- #


def test_read_intents_sort_to_reads():
    result = guard([{"op": "runway"}, {"op": "min_cash"}, {"op": "query_ledger"}])
    assert [r["op"] for r in result.reads] == ["runway", "min_cash", "query_ledger"]
    assert result.mutations == []


def test_change_intents_sort_to_mutations_and_are_validated():
    result = guard(
        [{"op": "add_item", "id": "gym", "direction": "out", "amount": "-49.90",
          "start": "2026-04-01"}]
    )
    assert result.writes
    assert result.mutations[0]["id"] == "gym"
    # Defaults from the typed grammar are filled in, so the stored card is complete.
    assert result.mutations[0]["recurrence"] == "1m"


def test_a_question_that_carries_changes_still_only_holds_them():
    """T11's shape: the model answers a question *and* emits writes.

    Both survive the guard, in separate lists. The changes go to the proposal
    path; nothing is applied here. ADR-0029 wanted exactly this — the
    unexpected operations surface on the confirmation card instead of landing.
    """
    result = guard(
        [
            {"op": "project_balance", "delta": "-1500.00", "delta_date": "2026-09-15"},
            {"op": "add_event", "date": "2026-09-15", "amount": "-1500.00"},
        ]
    )
    assert [r["op"] for r in result.reads] == ["project_balance"]
    assert [m["op"] for m in result.mutations] == ["add_event"]


# --- what the model cannot reach ------------------------------------------- #


def test_every_host_operation_is_refused_by_name():
    for name in sorted(HOST_OPS):
        result = guard([{"op": name, "amount": "1.00", "date": "2026-04-01",
                         "start": "2026-01-01", "end": "2027-01-01", "event": "e",
                         "item": "i", "action": "add"}])
        assert result.mutations == [], name
        assert result.reads == [], name
        assert result.diagnostics, name
        assert "interface" in result.diagnostics[0].message


def test_an_invented_operation_is_refused():
    result = guard([{"op": "delete_everything"}])
    assert result.mutations == []
    assert result.diagnostics[0].code == "CK-E901"


def test_a_raw_sdk_verb_is_refused():
    """`set_item`, `add_derived` and friends are not the model's vocabulary."""
    for name in ("set_item", "set_param", "add_derived", "correct_event", "void_event"):
        result = guard([{"op": name}])
        assert result.mutations == [], name
        assert result.diagnostics, name


def test_save_is_reported_and_never_proposed():
    """M9 is expressible; committing stays the user's own action (D-MLP-18)."""
    result = guard([{"op": "save", "message": "march"}])
    assert result.mutations == []
    assert [d["op"] for d in result.deferred] == ["save"]
    assert result.diagnostics[0].severity == "info"
    assert "Save" in result.diagnostics[0].message


def test_a_malformed_change_is_a_diagnostic_not_a_crash():
    result = guard([{"op": "add_item", "id": "gym"}])  # no direction, no amount, no start
    assert result.mutations == []
    assert result.diagnostics[0].code == "CK-E902"
    assert "direction" in result.diagnostics[0].message


def test_a_float_amount_is_refused():
    """No float ever enters the money path, not even from the model."""
    result = guard(
        [{"op": "add_item", "id": "gym", "direction": "out", "amount": -49.9,
          "start": "2026-04-01"}]
    )
    assert result.mutations == []
    assert result.diagnostics


def test_an_operation_carrying_as_of_is_refused():
    """ADR-0019 rule 2: ``as_of`` is host-filled and is not a slot."""
    result = guard(
        [{"op": "add_item", "id": "gym", "direction": "out", "amount": "-1.00",
          "start": "2026-04-01", "as_of": "2026-03-17"}]
    )
    assert result.mutations == []
    assert result.diagnostics[0].code == "CK-E902"


def test_an_operation_carrying_a_status_is_refused():
    """SPEC §5-F5: the model never chooses whether something happened."""
    result = guard(
        [{"op": "add_event", "date": "2026-02-10", "amount": "-134.09", "status": "actual"}]
    )
    assert result.mutations == []
    assert result.diagnostics[0].code == "CK-E902"


def test_nonsense_shapes_do_not_crash_the_turn():
    assert guard(None).all_operations() == []
    assert guard("some text").diagnostics
    assert guard([42, "x"]).diagnostics
    assert guard([]).all_operations() == []


def test_the_intent_key_is_accepted_as_well_as_op():
    """The read-intent executor accepts either; the guard normalizes to ``op``."""
    result = guard([{"intent": "runway"}])
    assert result.reads == [{"op": "runway"}]
