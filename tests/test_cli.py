"""Phase 10 — the CLI (PRD §8.3, §8.4, §8.6).

``cashkit doctor --json`` must be runnable by an agent as its first action and
must return structured JSON (§8.4). That shapes the whole surface: every command
accepts ``--json``, every JSON payload is the same structure the SDK returns,
and money is a decimal **string** rather than a float — a printed forecast that
has been through a binary fraction is the failure this project ranks worst.

``serve --quack`` is asserted to be *off* by default (PRD §3.4: Quack is
``core_nightly`` until DuckDB v2.0, "do not make any workflow depend on it").
The test that matters is the refusal, not the server.
"""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import pytest

from cashkit.cli import EXIT_DIAGNOSTIC, EXIT_OK, EXIT_USAGE, main
from cashkit.cli.main import QUACK_FLAG_ENV, resolve_holidays
from cashkit.model import PeriodRange
from cashkit.sdk import CashKit


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def as_json(text: str) -> dict:
    return json.loads(text)


@pytest.fixture()
def book_root(tmp_path: Path) -> Path:
    root = tmp_path / "acme"
    code, _, err = run(
        "init",
        str(root),
        "--horizon",
        "2026-01-01:2027-01-01",
        "--opening-balance",
        "250000.00",
        "--calendar",
        "IT",
        "--id",
        "acme-cashflow",
    )
    assert code == EXIT_OK, err
    return root


class TestInit:
    def test_it_creates_the_layout_and_an_initial_revision(self, book_root: Path) -> None:
        for path in (
            ".cashkit/version",
            ".cashkit/config.toml",
            ".gitignore",
            "book.yaml",
            "params.yaml",
            "scenarios/base.yaml",
            "snapshots/base.summary.yaml",
        ):
            assert (book_root / path).is_file(), path
        kit, _ = CashKit.open(book_root)
        assert kit is not None
        assert kit.book.id == "acme-cashflow"
        assert len(kit.history()) == 1

    def test_it_resolves_and_commits_the_holiday_set(self, book_root: Path) -> None:
        """ADR-0010: the holidays package is a seed; the runtime never reads it."""
        kit, _ = CashKit.open(book_root)
        assert kit is not None
        holidays = kit.book.calendar.holidays
        assert holidays, "a country calendar must resolve to committed dates"
        assert date(2026, 1, 1) in holidays
        assert all(
            kit.book.horizon.start <= day < kit.book.horizon.end for day in holidays
        )
        assert "holidays" in (book_root / "book.yaml").read_text(encoding="utf-8")

    def test_an_unknown_country_is_not_a_reason_to_fail(self) -> None:
        assert resolve_holidays(
            "ZZ", PeriodRange(start=date(2026, 1, 1), end=date(2027, 1, 1))
        ) == []
        assert resolve_holidays(None, PeriodRange(start=date(2026, 1, 1), end=date(2027, 1, 1))) == []

    def test_cutover_defaults_to_the_horizon_start_and_never_to_today(
        self, book_root: Path
    ) -> None:
        kit, _ = CashKit.open(book_root)
        assert kit is not None
        assert kit.book.cutover == date(2026, 1, 1)

    def test_it_refuses_to_create_a_second_book_in_the_same_place(
        self, book_root: Path
    ) -> None:
        code, _, err = run(
            "init", str(book_root), "--horizon", "2026-01-01:2027-01-01",
            "--opening-balance", "1",
        )
        assert code == EXIT_USAGE
        assert "already exists" in err

    def test_a_float_looking_amount_with_too_many_places_is_refused(
        self, tmp_path: Path
    ) -> None:
        code, _, err = run(
            "init", str(tmp_path / "b"), "--horizon", "2026-01-01:2027-01-01",
            "--opening-balance", "100.123456",
        )
        assert code == EXIT_USAGE
        assert "4 decimal places" in err

    def test_a_malformed_horizon_is_refused_with_the_expected_shape(
        self, tmp_path: Path
    ) -> None:
        code, _, err = run(
            "init", str(tmp_path / "b"), "--horizon", "2026", "--opening-balance", "1"
        )
        assert code == EXIT_USAGE
        assert "YYYY-MM-DD" in err

    def test_no_commit_creates_the_layout_only(self, tmp_path: Path) -> None:
        root = tmp_path / "uncommitted"
        code, _, _ = run(
            "init", str(root), "--horizon", "2026-01-01:2027-01-01",
            "--opening-balance", "0", "--no-commit",
        )
        assert code == EXIT_OK
        kit, _ = CashKit.open(root)
        assert kit is not None and kit.history() == []


class TestDoctor:
    def test_json_is_structured_and_complete(self, book_root: Path) -> None:
        code, out, _ = run("doctor", str(book_root), "--json")
        assert code == EXIT_OK
        payload = as_json(out)
        for key in (
            "cashkit_version",
            "engine_version",
            "schema_version",
            "python",
            "book_present",
            "extras",
            "ok",
            "problems",
            "revision",
            "working_tree_clean",
        ):
            assert key in payload, key
        assert payload["book_present"] is True
        assert payload["ok"] is True
        assert payload["problems"] == []

    def test_an_agent_can_run_it_first_in_an_empty_directory(
        self, tmp_path: Path
    ) -> None:
        """§9.6 step 1: run doctor, then decide. 'No book here' is an answer."""
        code, out, _ = run("doctor", str(tmp_path / "nothing"), "--json")
        assert code == EXIT_OK
        payload = as_json(out)
        assert payload["book_present"] is False
        assert payload["ok"] is False
        assert any("cashkit init" in problem for problem in payload["problems"])

    def test_it_reports_the_extras_rather_than_assuming_them(
        self, book_root: Path
    ) -> None:
        payload = as_json(run("doctor", str(book_root), "--json")[1])
        assert set(payload["extras"]) == {"duckdb", "git"}
        assert all(isinstance(value, bool) for value in payload["extras"].values())

    def test_human_output_says_the_same_things(self, book_root: Path) -> None:
        _, out, _ = run("doctor", str(book_root))
        assert "book_present" in out and "extra git" in out


class TestValidateRunStatusCommitHistory:
    def test_validate_is_json_and_counts_by_severity(self, book_root: Path) -> None:
        code, out, _ = run("validate", str(book_root), "--json")
        assert code == EXIT_OK
        payload = as_json(out)
        assert payload["errors"] == 0
        assert set(payload) >= {"scenario", "diagnostics", "errors", "warnings", "infos"}

    def test_validate_exits_non_zero_on_an_error(self, tmp_path: Path) -> None:
        root = tmp_path / "broken"
        run("init", str(root), "--horizon", "2026-01-01:2027-01-01",
            "--opening-balance", "0", "--no-commit")
        # Author a book the engine will refuse, through the store, then reopen.
        book_yaml = root / "items"
        book_yaml.mkdir(exist_ok=True)
        (book_yaml / "broken.yaml").write_text(
            'id: "broken"\nname: "Broken"\nkind: "derived"\n'
            'tags: {}\nflags: []\ncurrency: "EUR"\nsegments: []\n'
            'formula: "it(\\"ghost\\")"\nagg_rule: "sum"\n',
            encoding="utf-8",
        )
        code, out, _ = run("validate", str(root), "--json")
        assert code == EXIT_DIAGNOSTIC
        assert "CK-E001" in out

    def test_run_prints_a_summary_and_carries_no_float(self, book_root: Path) -> None:
        code, out, _ = run("run", "base", "--path", str(book_root), "--json")
        assert code == EXIT_OK
        payload = as_json(out)
        summary = payload["summary"]
        assert summary["opening_balance"] == "250000.00"
        for key in ("min_cash", "closing_balance", "net_cash", "total_inflow"):
            assert isinstance(summary[key], str), key
        # Exact strings, not rounded renderings of something else.
        assert summary["closing_balance"] == "250000.0000"

    def test_run_at_a_coarser_grain(self, book_root: Path) -> None:
        payload = as_json(
            run("run", "base", "--path", str(book_root), "--grain", "month", "--json")[1]
        )
        assert payload["summary"]["grain"] == "month"
        assert payload["summary"]["periods"] == 12

    def test_status_commit_and_history_round_trip(self, book_root: Path) -> None:
        code, out, _ = run("status", str(book_root), "--json")
        assert code == EXIT_OK and as_json(out)["clean"] is True

        # A second commit with nothing changed is reported honestly.
        code, out, _ = run("commit", str(book_root), "-m", "no change", "--json")
        assert code == EXIT_OK
        assert as_json(out)["committed"] is False

        # Change something on disk the way a human editing YAML would.
        params = book_root / "params.yaml"
        params.write_text('params:\n  "growth": "0.10"\n', encoding="utf-8")
        payload = as_json(run("status", str(book_root), "--json")[1])
        assert payload["clean"] is False
        assert payload["params_changed"] == ["growth"]

        code, out, _ = run("commit", str(book_root), "-m", "add growth", "--json")
        assert code == EXIT_OK and as_json(out)["committed"] is True

        payload = as_json(run("history", "--path", str(book_root), "--json")[1])
        assert [r["message"] for r in payload["revisions"]] == [
            "add growth",
            "initial commit",
        ]

    def test_history_narrows_to_an_item(self, book_root: Path) -> None:
        payload = as_json(
            run("history", "no_such_item", "--path", str(book_root), "--json")[1]
        )
        assert payload["revisions"] == []

    def test_every_command_refuses_a_missing_book_the_same_way(
        self, tmp_path: Path
    ) -> None:
        absent = str(tmp_path / "absent")
        for argv in (
            ["validate", absent],
            ["status", absent],
            ["commit", absent, "-m", "x"],
            ["describe", absent],
            ["run", "base", "--path", absent],
            ["history", "--path", absent],
        ):
            code, _, err = run(*argv)
            assert code == EXIT_USAGE, argv
            assert "CK-E029" in err, argv


class TestDescribe:
    def test_it_emits_the_query_vocabulary_as_json_by_default(
        self, book_root: Path
    ) -> None:
        code, out, _ = run("describe", str(book_root))
        assert code == EXIT_OK
        payload = as_json(out)
        assert payload["pivot"]["index"] == ["period", "item"]
        assert payload["measures"] == ["accrual", "cash"]
        assert payload["book_id"] == "acme-cashflow"
        assert isinstance(payload["opening_balance"], str)

    def test_text_mode_is_a_rendering_of_the_same_structure(
        self, book_root: Path
    ) -> None:
        _, out, _ = run("describe", str(book_root), "--text")
        assert "measures accrual, cash" in out


class TestServeIsFeatureFlagged:
    def test_it_refuses_by_default_and_says_why(self, book_root: Path) -> None:
        code, _, err = run("serve", str(book_root), "--quack")
        assert code == EXIT_DIAGNOSTIC
        assert "core_nightly" in err
        assert "Parquet" in err

    def test_the_refusal_is_machine_parseable(self, book_root: Path) -> None:
        code, out, _ = run("serve", str(book_root), "--quack", "--json")
        assert code == EXIT_DIAGNOSTIC
        payload = as_json(out)
        assert payload["started"] is False
        assert QUACK_FLAG_ENV in payload["enable_with"]
        assert payload["stable_alternative"]

    def test_enabling_the_flag_reaches_duckdb_and_reports_what_it_found(
        self, book_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With the flag on, the refusal must come from DuckDB rather than from
        the flag — and it must still be a structured report, not a traceback."""
        monkeypatch.setenv(QUACK_FLAG_ENV, "1")
        code, out, err = run("serve", str(book_root), "--quack", "--json")
        payload = as_json(out)
        assert payload["experimental"] is True
        if not payload["started"]:
            assert payload["reason"]
            assert code == EXIT_DIAGNOSTIC
        assert "Traceback" not in err

    def test_serve_without_quack_is_a_usage_error(self, book_root: Path) -> None:
        code, _, err = run("serve", str(book_root))
        assert code == EXIT_USAGE
        assert "--quack" in err


class TestJsonNeverCarriesAFloat:
    def test_across_every_json_command(self, book_root: Path) -> None:
        """Non-negotiable: no float for money anywhere, CLI rendering included."""
        payloads = [
            run("doctor", str(book_root), "--json")[1],
            run("validate", str(book_root), "--json")[1],
            run("run", "base", "--path", str(book_root), "--json")[1],
            run("status", str(book_root), "--json")[1],
            run("history", "--path", str(book_root), "--json")[1],
            run("describe", str(book_root))[1],
        ]

        def walk(node, where: str) -> None:
            if isinstance(node, float):
                raise AssertionError(f"float in CLI JSON at {where}: {node}")
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{where}.{key}")
            if isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{where}[{index}]")

        for index, text in enumerate(payloads):
            walk(json.loads(text), f"payload{index}")

    def test_a_decimal_reaches_json_as_its_exact_string(self, tmp_path: Path) -> None:
        root = tmp_path / "exact"
        run("init", str(root), "--horizon", "2026-01-01:2027-01-01",
            "--opening-balance", "1000.1000")
        payload = as_json(run("run", "base", "--path", str(root), "--json")[1])
        assert payload["summary"]["opening_balance"] == "1000.1000"
