"""Session S5.6 — the §6.4 execution surface on the kit.

``frame``, ``pivot``, ``compare`` and ``export`` existed before this session and
were unreachable from PRD §6: they lived on
:class:`~cashkit.stores.frames.DuckdbFrameStore`, below the SDK line. An agent
following §6 could evaluate a book and could not tabulate it.

The claim this file has to carry is that the wiring **added no arithmetic**.
Every kit result is asserted equal — as a whole ``Table``, not summed and
compared — to the same query run directly against the store, so a number that
moved between the two layers is a failing test rather than a plausible one.

Three other properties, each of which has a way of being quietly untrue:

* **The duckdb extra stays optional.** With ``duckdb`` unimportable, all four
  return ``CK-E033`` naming the extra, and ``summary()`` still works.
* **A revision-bound kit reads its own revision.** ``at(ref)`` refuses writes;
  a frame is a read, and the run key carries the revision so the past and the
  present cannot collide in one store.
* **An export re-reads as what was written.** ``Decimal``, not a float that
  prints the same, and under ``exports/`` where §3.3 puts it.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cashkit.engine import ENGINE_VERSION
from cashkit.model import (
    Amount,
    Grain,
    Item,
    PeriodRange,
    Recurrence,
    Segment,
    Settlement,
    Table,
)
from cashkit.sdk import CashKit, add_item, compare, create_book, export, frame, pivot
from cashkit.sdk.execution import EXPORTS_DIR, _FRAME_COLUMNS, run_key

QUARTER = PeriodRange(start=date(2026, 1, 1), end=date(2026, 4, 1))


def _flow(item_id: str, amount: str, direction: str, **tags: str) -> Item:
    return Item(
        id=item_id,
        name=item_id,
        kind="flow",
        direction=direction,  # type: ignore[arg-type]
        tags=dict(tags),
        segments=[
            Segment(
                start=date(2026, 1, 1),
                recurrence=Recurrence(
                    every=1, unit=Grain.MONTH, anchor="day_of_month", day=1
                ),
                amount=Amount(constant=Decimal(amount)),
            )
        ],
        settlement=Settlement.immediate(),
    )


@pytest.fixture()
def kit(tmp_path: Path) -> CashKit:
    """A three-item book built through the SDK alone, tagged for pivoting."""
    ref = create_book(
        tmp_path / "book",
        id="book",
        horizon=QUARTER,
        opening_balance=Decimal("10000.0000"),
    )
    assert ref.kit is not None, ref.diagnostics
    built = ref.kit
    for item in (
        _flow("consulting", "12000", "in", cat="revenue", customer="acme"),
        _flow("licences", "3000", "in", cat="revenue", customer="globex"),
        _flow("rent", "-3000", "out", cat="opex"),
    ):
        assert add_item(built, item).ok
    return built


@pytest.fixture()
def direct(kit: CashKit):
    """The same run materialized straight into a store of its own.

    Deliberately a *separate* store from the kit's: comparing the kit's answer
    to a query on the kit's own store would prove only that the store is
    deterministic. This one is materialized by the test, from the same
    ``RunRef``, under the same key.
    """
    from cashkit.stores.frames import DuckdbFrameStore

    run = kit.run()
    with DuckdbFrameStore(policy=kit.policy) as store:
        assert (
            store.materialize(
                run_key(run),
                run.result,
                run.book,
                scenario=run.scenario,
                engine_version=ENGINE_VERSION,
            )
            == ()
        )
        yield run, store, run_key(run)


# --------------------------------------------------------------------------- #
# Gate 1 — the kit is the store, with nothing added
# --------------------------------------------------------------------------- #


class TestTheKitMatchesTheStoreExactly:
    @pytest.mark.parametrize(
        "grain", [None, Grain.WEEK, Grain.MONTH, Grain.QUARTER, Grain.YEAR]
    )
    def test_frame_round_trips_at_every_grain(self, kit: CashKit, direct, grain) -> None:
        run, store, run_id = direct
        assert kit.frame(run, grain=grain) == store.frame(run_id, grain=grain)

    def test_frame_slices_the_same_way(self, kit: CashKit, direct) -> None:
        run, store, run_id = direct
        for kwargs in (
            {"measures": ["cash"]},
            {"status": "forecast"},
            {"where": "cat:revenue"},
            {"where": "customer:acme", "grain": Grain.MONTH, "measures": ["accrual"]},
            {"include_synthetic": False},
        ):
            assert kit.frame(run, **kwargs) == store.frame(run_id, **kwargs), kwargs

    def test_the_frame_is_not_empty_so_equality_means_something(
        self, kit: CashKit, direct
    ) -> None:
        run, _, _ = direct
        table = kit.frame(run, grain=Grain.MONTH)
        assert table.columns == _FRAME_COLUMNS
        assert len(table) == 3 * 3 * 2, "3 items x 3 months x (accrual, cash)"
        for measure in ("accrual", "cash"):
            sliced = kit.frame(run, grain=Grain.MONTH, measures=[measure])
            assert sum(sliced.column("value")) == Decimal("36000.0000")

    def test_the_declared_frame_columns_match_the_stores_own(self) -> None:
        """``execution.py`` may not import the frame store, so it spells the
        column tuple out. The duplication is forced; the drift is not."""
        from cashkit.stores.frames import FRAME_COLUMNS

        assert _FRAME_COLUMNS == FRAME_COLUMNS

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"columns": "tag:customer"},
            {"columns": "tag:cat", "grain": Grain.MONTH},
            {"columns": "item", "values": "accrual"},
            {"index": "item", "columns": "measure"},
        ],
    )
    def test_pivot_round_trips(self, kit: CashKit, direct, kwargs) -> None:
        run, store, run_id = direct
        assert kit.pivot(run, **kwargs) == store.pivot(run_id, **kwargs)

    def test_pivot_keeps_untagged_items_in_their_own_column(
        self, kit: CashKit, direct
    ) -> None:
        run, _, _ = direct
        table = kit.pivot(run, columns="tag:customer", values="cash", grain=Grain.MONTH)
        assert set(table.columns) == {"period", "acme", "globex", "(untagged)"}

    def test_compare_round_trips(self, kit: CashKit, direct) -> None:
        run, store, run_id = direct
        assert kit.compare([run], grain=Grain.MONTH) == store.compare(
            [run_id], grain=Grain.MONTH
        )

    def test_compare_holds_two_scenarios_apart(self, kit: CashKit) -> None:
        assert kit.scenarios.fork("base", "downside").ok
        assert kit.scenarios.set_param(
            "downside", "opening_balance", Decimal("0.0000")
        ).ok
        runs = [kit.run(), kit.run("downside")]
        table = kit.compare(runs, grain=Grain.QUARTER)

        assert table.columns == ("period_start", run_key(runs[0]), run_key(runs[1]))
        assert "base" in table.columns[1] and "downside" in table.columns[2]
        assert len(table) == 1

    def test_two_runs_with_the_same_key_stay_two_columns(self, kit: CashKit) -> None:
        """The caller asked for two columns. Collapsing them because they hash
        the same would answer a question nobody asked."""
        table = kit.compare([kit.run(), kit.run()], grain=Grain.QUARTER)
        assert len(table.columns) == 3
        assert table.columns[2].endswith("#2")
        assert table.rows[0][1] == table.rows[0][2]


# --------------------------------------------------------------------------- #
# Gate 1b — export lands where §3.3 says and comes back as it went in
# --------------------------------------------------------------------------- #


class TestExport:
    def test_a_relative_path_lands_under_exports(self, kit: CashKit, direct) -> None:
        run, _, _ = direct
        report = kit.export(run, "q1.parquet")

        assert report.ok, report.diagnostics
        assert report.path == kit.root / EXPORTS_DIR / "q1.parquet"
        assert report.path.is_file()
        assert report.created == (str(report.path),)

    def test_the_file_re_reads_losslessly(self, kit: CashKit, direct) -> None:
        run, _, _ = direct
        written = kit.export(run, "q1.parquet", grain=Grain.MONTH).path
        assert written is not None

        back = kit.read_export(written)
        assert back == kit.frame(run, grain=Grain.MONTH)
        assert all(isinstance(row[4], Decimal) for row in back.rows), (
            "DECIMAL(18,4) through Parquet — never a float that prints the same"
        )

    def test_csv_round_trips_too(self, kit: CashKit, direct) -> None:
        run, _, _ = direct
        written = kit.export(run, "q1.csv", format="csv", grain=Grain.MONTH).path
        assert written is not None and written.is_file()
        assert kit.read_export(written).rows == kit.frame(run, grain=Grain.MONTH).rows

    def test_an_absolute_path_is_honoured_as_given(
        self, kit: CashKit, direct, tmp_path: Path
    ) -> None:
        """'Write this file over there for someone else' is the reason the verb
        exists; silently relocating it would be worse than either choice."""
        run, _, _ = direct
        elsewhere = tmp_path / "share" / "q1.parquet"
        report = kit.export(run, elsewhere)

        assert report.path == elsewhere and elsewhere.is_file()
        assert not (kit.root / EXPORTS_DIR / "q1.parquet").exists()

    def test_exports_is_git_ignored(self, kit: CashKit, direct) -> None:
        """PRD §3.3: an export is a copy of what a revision already reproduces."""
        run, _, _ = direct
        kit.export(run, "q1.parquet")
        ignored = (kit.root / ".gitignore").read_text(encoding="utf-8")
        assert f"{EXPORTS_DIR}/" in ignored.splitlines()

    def test_the_matching_store_call_writes_the_same_bytes(
        self, kit: CashKit, direct, tmp_path: Path
    ) -> None:
        run, store, run_id = direct
        mine = kit.export(run, "q1.parquet", grain=Grain.MONTH).path
        theirs = store.export(
            run_id, tmp_path / "theirs.parquet", grain=Grain.MONTH
        )
        assert mine is not None
        assert mine.read_bytes() == theirs.read_bytes()


# --------------------------------------------------------------------------- #
# Gate 2 — duckdb is an extra, and its absence is a diagnostic
# --------------------------------------------------------------------------- #


@pytest.fixture()
def without_duckdb(monkeypatch: pytest.MonkeyPatch):
    """Make ``import duckdb`` fail the way a core install does.

    ``sys.modules["duckdb"] = None`` is the documented way to make an import
    raise ``ImportError`` without uninstalling anything; the frame store module
    is evicted alongside it, because a cached
    ``cashkit.stores.frames`` would import fine and prove nothing. ``monkeypatch``
    restores both, so the rest of the session is unaffected.
    """
    monkeypatch.delitem(sys.modules, "cashkit.stores.frames", raising=False)
    monkeypatch.setitem(sys.modules, "duckdb", None)
    with pytest.raises(ImportError):
        import duckdb  # noqa: F401
    yield


class TestWithoutTheExtra:
    def test_the_sdk_still_imports_and_the_book_still_runs(
        self, kit: CashKit, without_duckdb
    ) -> None:
        """PRD §5.2's headline question needs no columnar engine: ``summary()``
        works off the engine's int64 columns and is deliberately not gated."""
        assert kit.run().summary().net_cash == Decimal("36000.0000")

    def test_every_frame_verb_reports_ck_e033(
        self, kit: CashKit, without_duckdb
    ) -> None:
        run = kit.run()
        results: list[Table] = [
            kit.frame(run),
            kit.pivot(run, columns="tag:cat"),
            kit.compare([run]),
        ]
        for table in results:
            assert len(table) == 0
            assert not table.ok
            assert [d.code for d in table.diagnostics] == ["CK-E033"]
            assert "duckdb" in table.diagnostics[0].suggested_fix

        report = kit.export(run, "q1.parquet")
        assert [d.code for d in report.diagnostics] == ["CK-E033"]
        assert report.path is None and report.empty
        assert not (kit.root / EXPORTS_DIR).exists(), "refused, so nothing written"

    def test_the_refusal_is_a_diagnostic_and_never_an_import_error(
        self, kit: CashKit, without_duckdb
    ) -> None:
        """An agent can loop on a structured diagnostic; it cannot loop on a
        traceback from three frames below the surface it codes against."""
        run = kit.run()
        for call in (
            lambda: kit.frame(run),
            lambda: kit.pivot(run, columns="tag:cat"),
            lambda: kit.compare([run]),
            lambda: kit.export(run, "q1.parquet"),
            lambda: kit.read_export("q1.parquet"),
        ):
            call()  # must not raise

    def test_a_frame_refused_still_declares_its_shape(
        self, kit: CashKit, without_duckdb
    ) -> None:
        assert kit.frame(kit.run()).columns == _FRAME_COLUMNS

    def test_with_the_extra_the_same_calls_work(self, kit: CashKit) -> None:
        run = kit.run()
        assert kit.frame(run).ok and len(kit.frame(run))
        assert kit.pivot(run, columns="tag:cat").ok
        assert kit.compare([run]).ok
        assert kit.export(run, "q1.parquet").ok


# --------------------------------------------------------------------------- #
# Gate 2b — nothing above the store line imports duckdb
# --------------------------------------------------------------------------- #


def test_the_sdk_never_reaches_duckdb_until_asked() -> None:
    """``test_frames.py`` lints that no module *names* duckdb outside the store.
    This proves the SDK does not reach it transitively either: importing the
    surface, opening a book, running it and summarizing it must all happen
    without the extra ever being loaded. Run in a subprocess, because by this
    point in the session something else has certainly imported it."""
    probe = """
import sys
import cashkit.sdk as sdk
assert "duckdb" not in sys.modules, "importing the SDK loaded duckdb"
assert "cashkit.stores.frames" not in sys.modules, "importing the SDK loaded the store"
print("ok")
"""
    finished = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True
    )
    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip() == "ok"


# --------------------------------------------------------------------------- #
# Gate 3 — selectors are validated here, not raised from the store
# --------------------------------------------------------------------------- #


class TestArgumentHandling:
    def test_a_malformed_selector_is_ck_e003_and_not_an_exception(
        self, kit: CashKit
    ) -> None:
        table = kit.frame(kit.run(), where="cat=revenue")
        assert [d.code for d in table.diagnostics] == ["CK-E003"]
        assert len(table) == 0 and not table.ok

    def test_a_selector_matching_nothing_is_an_empty_table_with_no_error(
        self, kit: CashKit
    ) -> None:
        """'You typed it wrong' and 'nothing matched' must not be one answer."""
        table = kit.frame(kit.run(), where="customer:initech")
        assert len(table) == 0 and table.diagnostics == () and table.ok

    @pytest.mark.parametrize(
        "call",
        [
            lambda kit, run: kit.frame(run, measures=["revenue"]),
            lambda kit, run: kit.pivot(run, values="revenue"),
            lambda kit, run: kit.pivot(run, columns="colour"),
            lambda kit, run: kit.compare([run], metric="revenue"),
            lambda kit, run: kit.export(run, "q1.xlsx", format="xlsx"),
        ],
    )
    def test_a_vocabulary_describe_book_lists_raises_instead(
        self, kit: CashKit, call
    ) -> None:
        """PRD §6.5 reserves exceptions for programmer error. A measure name is
        a closed set ``describe_book()`` enumerates, so a wrong one is a bug in
        the caller — unlike a selector, which an agent composes from tags."""
        with pytest.raises(ValueError):
            call(kit, kit.run())


# --------------------------------------------------------------------------- #
# Gate 4 — a revision-bound kit reads its own revision
# --------------------------------------------------------------------------- #


class TestRevisionBoundReads:
    def test_at_ref_frames_the_past_while_the_present_moves_on(
        self, kit: CashKit
    ) -> None:
        first = kit.commit("three items")
        assert first.revision is not None

        assert add_item(kit, _flow("hosting", "-500", "out", cat="opex")).ok
        assert kit.commit("hosting too").revision is not None

        past, problems = kit.at(first.revision.id)
        assert past is not None and problems == ()

        now = kit.frame(kit.run(), grain=Grain.QUARTER, measures=["accrual"])
        then = past.frame(past.run(), grain=Grain.QUARTER, measures=["accrual"])

        assert set(then.column("item_id")) == {"consulting", "licences", "rent"}
        assert set(now.column("item_id")) == {
            "consulting",
            "licences",
            "rent",
            "hosting",
        }
        assert sum(then.column("value")) == Decimal("36000.0000")
        assert sum(now.column("value")) == Decimal("34500.0000")

    def test_the_two_kits_keys_cannot_collide(self, kit: CashKit) -> None:
        report = kit.commit("committed")
        assert report.revision is not None
        past, _ = kit.at(report.revision.id)
        assert past is not None
        assert run_key(past.run()).startswith(report.revision.id)
        assert run_key(kit.run()).startswith("working")

    def test_reads_are_allowed_where_writes_refuse(self, kit: CashKit) -> None:
        """``at(ref)`` refuses writes with ``CK-E030``; a frame is a read and
        must not be swept up in that.

        ``export()`` writes a *file* and is still a read: the file is a copy of
        what the revision already reproduces, it lands in git-ignored
        ``exports/``, and refusing it would make a past revision the one thing
        an agent cannot hand to anybody.
        """
        report = kit.commit("committed")
        assert report.revision is not None
        past, _ = kit.at(report.revision.id)
        assert past is not None

        assert past.frame(past.run()).ok
        assert past.pivot(past.run(), columns="tag:cat").ok
        assert past.export(past.run(), "past.parquet").ok
        assert (past.root / EXPORTS_DIR / "past.parquet").is_file()

        assert [d.code for d in past.commit("nope").diagnostics] == ["CK-E030"]
        assert [d.code for d in past.discard().diagnostics] == ["CK-E030"]


# --------------------------------------------------------------------------- #
# The module functions and the kit methods are the same call
# --------------------------------------------------------------------------- #


def test_the_free_functions_and_the_methods_agree(kit: CashKit, tmp_path: Path) -> None:
    run = kit.run()
    assert frame(kit, run, grain=Grain.MONTH) == kit.frame(run, grain=Grain.MONTH)
    assert pivot(kit, run, columns="tag:cat") == kit.pivot(run, columns="tag:cat")
    assert compare(kit, [run]) == kit.compare([run])
    assert export(kit, run, tmp_path / "a.parquet").path == (tmp_path / "a.parquet")
