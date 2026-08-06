"""The command-line interface (PRD §8.3, §8.4, §8.6).

Eight commands: ``init``, ``doctor``, ``validate``, ``run``, ``status``,
``commit``, ``history``, ``serve``, plus ``describe`` — the §9.6 entry point an
agent uses before it writes a query.

Three rules shape the whole module:

* **Everything is machine-parseable.** ``--json`` is available on every command,
  not only ``doctor``, and the JSON shape is the same one the SDK returns.
  Human text is a rendering of the same structure, never a separate story.
* **No float, ever.** Money reaches JSON as a decimal *string*
  (:func:`_encode`), because ``json.dumps(Decimal(...))`` has no float-free
  default and a printed forecast that has been through a binary fraction is the
  failure this project ranks worst. The same rule holds for the text output.
* **Errors are data.** A command that fails prints diagnostics with their
  ``suggested_fix`` and exits non-zero; it does not raise a traceback at a user.
  :data:`EXIT_DIAGNOSTIC` distinguishes "your book has errors" from
  :data:`EXIT_USAGE`, "you asked for something that makes no sense".

``serve --quack`` is gated behind an explicit feature flag (PRD §3.4: Quack is
``core_nightly`` until DuckDB v2.0, "do not make any workflow depend on it").
Without the flag it refuses and says why; Parquet export is the stable path.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel

import cashkit
from cashkit.engine import ENGINE_VERSION
from cashkit.model import CalendarSpec, Diagnostic, Grain, PeriodRange
from cashkit.sdk import CashKit, create_book, resolve_holidays  # noqa: F401
from cashkit.stores.config import SCHEMA_VERSION, EngineSettings, is_book_root

__all__ = ["EXIT_DIAGNOSTIC", "EXIT_OK", "EXIT_USAGE", "QUACK_FLAG_ENV", "main"]

EXIT_OK = 0
#: The book has error-severity diagnostics, or the operation was refused.
EXIT_DIAGNOSTIC = 1
#: The invocation itself was wrong: bad arguments, no book at the path.
EXIT_USAGE = 2

#: Environment variable enabling the experimental Quack server (PRD §3.4).
QUACK_FLAG_ENV = "CASHKIT_ENABLE_QUACK"


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def _encode(value: Any) -> Any:
    """JSON-safe conversion that never produces a float.

    ``Decimal`` becomes its exact decimal *string*: the alternative,
    ``float(value)``, silently reintroduces binary fractions at the one place a
    number leaves the system for a human to read.
    """
    if isinstance(value, BaseModel):
        return {name: _encode(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value) if isinstance(value, (set, frozenset)) else value
        return [_encode(item) for item in items]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: _encode(getattr(value, name)) for name in value.__dataclass_fields__
        }
    return str(value)


def _emit(stream, payload: Any) -> None:
    stream.write(json.dumps(_encode(payload), indent=2, sort_keys=True) + "\n")


def _render_diagnostics(stream, diagnostics: Sequence[Diagnostic]) -> None:
    if not diagnostics:
        stream.write("No diagnostics.\n")
        return
    for diagnostic in diagnostics:
        where = f" [{diagnostic.item_id}" + (
            f".{diagnostic.field}]" if diagnostic.field else "]"
        )
        stream.write(
            f"{diagnostic.severity.upper():7} {diagnostic.code}"
            f"{where if diagnostic.item_id else ''} {diagnostic.message}\n"
            f"        fix: {diagnostic.suggested_fix}\n"
        )


def _worst(diagnostics: Sequence[Diagnostic]) -> int:
    return (
        EXIT_DIAGNOSTIC
        if any(d.severity == "error" for d in diagnostics)
        else EXIT_OK
    )


# --------------------------------------------------------------------------- #
# Argument helpers
# --------------------------------------------------------------------------- #


def _parse_horizon(text: str) -> PeriodRange:
    start, _, end = text.partition(":")
    if not end:
        raise argparse.ArgumentTypeError(
            "--horizon must be 'YYYY-MM-DD:YYYY-MM-DD' (end exclusive)"
        )
    try:
        return PeriodRange(start=date.fromisoformat(start), end=date.fromisoformat(end))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid horizon {text!r}: {exc}") from exc


def _parse_money(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a decimal amount; pass digits, not a float literal"
        ) from exc
    if value.as_tuple().exponent < -4:  # type: ignore[operator]
        raise argparse.ArgumentTypeError(
            "money carries at most 4 decimal places (the engine is int64 minor "
            "units at 4 dp and must never round an authored amount silently)"
        )
    return value


def _open(path: str, stream) -> CashKit | None:
    kit, diagnostics = CashKit.open(path)
    if kit is None:
        _render_diagnostics(stream, diagnostics)
    return kit


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_init(args: argparse.Namespace, out, err) -> int:
    """Create the §3.3 layout, a base scenario and (unless refused) a commit.

    Every model this command needs is built by ``create_book()`` (PRD §6.1): the
    CLI is a caller of the SDK like any agent, and a second construction path
    that happened to agree today is a second one that can stop agreeing.
    """
    root = Path(args.path)
    horizon: PeriodRange = args.horizon
    cutover = args.cutover or horizon.start
    ref = create_book(
        root,
        id=args.id or _slug(root.name),
        horizon=horizon,
        opening_balance=args.opening_balance,
        grain=Grain(args.grain),
        calendar=CalendarSpec(
            fiscal_year_start_month=args.fiscal_year_start, country=args.calendar
        ),
        cutover=cutover,
        settings=EngineSettings(),
    )
    if ref.kit is None:
        _render_diagnostics(err, ref.diagnostics)
        return EXIT_USAGE
    kit = ref.kit
    book = kit.book
    report = None
    if not args.no_commit:
        report = kit.commit(args.message, author=args.author)
        if not report.ok:
            _render_diagnostics(err, report.diagnostics)
            return EXIT_DIAGNOSTIC
    payload = {
        "book_id": book.id,
        "root": str(root),
        "grain": book.base_grain.value,
        "horizon": {"start": horizon.start, "end": horizon.end},
        "cutover": cutover,
        "opening_balance": book.opening_balance,
        "currency": args.currency,
        "calendar": args.calendar or "",
        "holidays_resolved": len(book.calendar.holidays),
        "scenarios": ["base"],
        "revision": report.revision.id if report and report.revision else "",
        "schema_version": SCHEMA_VERSION,
    }
    if args.json:
        _emit(out, payload)
    else:
        out.write(
            f"Created book {book.id!r} at {root}\n"
            f"  horizon   {horizon.start} .. {horizon.end} (end exclusive), "
            f"grain {book.base_grain.value}\n"
            f"  cutover   {cutover} (the last reconciled boundary — never today)\n"
            f"  opening   {book.opening_balance} {args.currency}\n"
            f"  calendar  {args.calendar or 'none'}, "
            f"{len(book.calendar.holidays)} holidays resolved and committed\n"
            f"  scenarios base\n"
            f"  revision  {payload['revision'] or '(not committed)'}\n"
        )
    return EXIT_OK


def _slug(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in name.lower())
    cleaned = cleaned.strip("-") or "book"
    if not cleaned[0].isalpha():
        cleaned = f"b-{cleaned}"
    return cleaned


def cmd_doctor(args: argparse.Namespace, out, err) -> int:
    """Store connectivity, schema version, engine version, git state (§8.4).

    Runnable by an agent as its first action, and structured under ``--json``.
    It never fails on a missing book: reporting "there is no book here" *is* the
    answer, and an agent needs it to decide whether to create one (§9.6 rule 2).
    """
    root = Path(args.path)
    payload: dict[str, Any] = {
        "cashkit_version": cashkit.__version__,
        "engine_version": ENGINE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "python": sys.version.split()[0],
        "root": str(root),
        "book_present": is_book_root(root),
        "extras": _extras(),
        "ok": True,
        "problems": [],
    }
    problems: list[str] = []

    if payload["book_present"]:
        kit, diagnostics = CashKit.open(root)
        if kit is None:
            problems.append("the book at this path could not be read")
            payload["diagnostics"] = list(diagnostics)
        else:
            head = kit.revisions.head()
            payload["book_id"] = kit.book.id
            payload["scenarios"] = sorted(kit.scenarios.scenarios)
            payload["items"] = len(kit.book.items)
            payload["rounding_policy"] = kit.policy.value
            payload["revision"] = head.id if head else ""
            payload["revision_message"] = head.message if head else ""
            payload["revisions"] = len(kit.history(limit=10_000))
            payload["ledger_events"] = (
                len(kit.ledger.facts()) if kit.ledger is not None else 0
            )
            payload["working_tree_clean"] = kit.status().clean
            if head is None:
                problems.append("the history has no revisions yet — run 'cashkit commit'")
    else:
        problems.append(f"no CashKit book at {root} — run 'cashkit init {root}'")

    if not payload["extras"]["git"]:
        problems.append("the 'git' extra is not installed: pip install 'cashkit[git]'")

    payload["problems"] = problems
    payload["ok"] = not problems
    if args.json:
        _emit(out, payload)
    else:
        out.write(f"cashkit {payload['cashkit_version']} (engine {ENGINE_VERSION}, "
                  f"schema {SCHEMA_VERSION})\n")
        for key in ("root", "book_present", "book_id", "items", "revision", "working_tree_clean"):
            if key in payload:
                out.write(f"  {key:20} {payload[key]}\n")
        for name, present in sorted(payload["extras"].items()):
            out.write(f"  extra {name:14} {'installed' if present else 'missing'}\n")
        for problem in problems:
            out.write(f"  ! {problem}\n")
    # A missing book is a *reportable state*, not a failure of doctor.
    return EXIT_OK


def _extras() -> dict[str, bool]:
    found: dict[str, bool] = {}
    for name, module in (("duckdb", "duckdb"), ("git", "pygit2")):
        try:
            __import__(module)
            found[name] = True
        except ImportError:
            found[name] = False
    return found


def cmd_validate(args: argparse.Namespace, out, err) -> int:
    """Semantic diagnostics on the current book (§8.4)."""
    kit = _open(args.path, err)
    if kit is None:
        return EXIT_USAGE
    diagnostics = kit.validate(args.scenario)
    if args.json:
        _emit(
            out,
            {
                "scenario": args.scenario,
                "diagnostics": list(diagnostics),
                "errors": sum(1 for d in diagnostics if d.severity == "error"),
                "warnings": sum(1 for d in diagnostics if d.severity == "warning"),
                "infos": sum(1 for d in diagnostics if d.severity == "info"),
            },
        )
    else:
        _render_diagnostics(out, diagnostics)
    return _worst(diagnostics)


def cmd_run(args: argparse.Namespace, out, err) -> int:
    """Evaluate a scenario and print its summary (§8.4)."""
    kit = _open(args.path, err)
    if kit is None:
        return EXIT_USAGE
    run = kit.run(args.scenario)
    summary = run.summary(grain=Grain(args.grain) if args.grain else None)
    errors = [d for d in run.diagnostics if d.severity == "error"]
    if args.json:
        _emit(
            out,
            {
                "scenario": args.scenario,
                "summary": summary,
                "diagnostics": list(run.diagnostics),
            },
        )
    else:
        out.write(f"Run of {args.scenario!r} on book {summary.book_id!r}\n")
        for field in (
            "grain",
            "balance_source",
            "periods",
            "opening_balance",
            "closing_balance",
            "min_cash",
            "min_cash_period",
            "runway_end",
            "breakeven_period",
            "total_inflow",
            "total_outflow",
            "net_cash",
        ):
            out.write(f"  {field:18} {getattr(summary, field)}\n")
        if errors:
            out.write("\n")
            _render_diagnostics(out, errors)
    return _worst(run.diagnostics)


def cmd_status(args: argparse.Namespace, out, err) -> int:
    """Structured working-state diff (§8.4) — never a git porcelain string."""
    kit = _open(args.path, err)
    if kit is None:
        return EXIT_USAGE
    state = kit.status()
    if args.json:
        _emit(out, state)
    else:
        out.write(
            f"Working state vs revision {state.revision or '(none)'}: "
            f"{'clean' if state.clean else 'uncommitted changes'}\n"
        )
        for label, values in (
            ("items added", state.items_added),
            ("items removed", state.items_removed),
            ("items changed", state.items_changed),
            ("params changed", state.params_changed),
            ("book fields", state.book_fields_changed),
            ("scenarios", state.scenarios_changed),
            ("settings", state.settings_changed),
        ):
            if values:
                out.write(f"  {label:16} {', '.join(values)}\n")
        _render_diagnostics(out, state.diagnostics) if state.diagnostics else None
    return EXIT_OK


def cmd_commit(args: argparse.Namespace, out, err) -> int:
    """Commit the working state (§8.4) — wraps the SDK's ``commit()``."""
    kit = _open(args.path, err)
    if kit is None:
        return EXIT_USAGE
    report = kit.commit(args.message, author=args.author)
    payload = {
        "revision": report.revision.id if report.revision else "",
        "message": args.message,
        "committed": report.revision is not None,
        "diagnostics": list(report.diagnostics),
    }
    if args.json:
        _emit(out, payload)
    else:
        if report.revision is None:
            out.write("Nothing to commit: the tree is unchanged.\n")
        else:
            out.write(
                f"Committed {report.revision.short_id} {report.revision.message!r}\n"
                f"  {len(report.changed)} tracked file(s)\n"
            )
        _render_diagnostics(out, report.diagnostics)
    return _worst(report.diagnostics)


def cmd_history(args: argparse.Namespace, out, err) -> int:
    """Revision list (§8.4) — optionally narrowed to one item or scenario."""
    kit = _open(args.path, err)
    if kit is None:
        return EXIT_USAGE
    revisions = kit.history(
        item=args.item, scenario=args.scenario, field=args.field, limit=args.limit
    )
    if args.json:
        _emit(out, {"revisions": revisions})
    else:
        if not revisions:
            out.write("No revisions.\n")
        for revision in revisions:
            out.write(
                f"{revision.short_id}  {revision.timestamp}  {revision.author:12} "
                f"{revision.message}\n"
            )
    return EXIT_OK


def cmd_describe(args: argparse.Namespace, out, err) -> int:
    """The book's schema, items and query vocabulary (§6.5, §9.6).

    An agent's first read: every item id, tag key, tag value, measure, grain and
    ``pivot()`` argument this book accepts. A field name absent from it does not
    exist.
    """
    kit = _open(args.path, err)
    if kit is None:
        return EXIT_USAGE
    description = kit.describe_book(args.scenario)
    if args.json or not args.text:
        _emit(out, description)
    else:
        out.write(f"Book {description.book_id!r} ({description.base_grain} grain)\n")
        out.write(
            f"  horizon {description.horizon_start}..{description.horizon_end}, "
            f"cutover {description.cutover}\n"
        )
        out.write(f"  measures {', '.join(description.measures)}\n")
        out.write(f"  grains   {', '.join(description.grains)}\n")
        out.write(f"  params   {', '.join(sorted(description.params)) or '(none)'}\n")
        for item in description.items:
            out.write(f"  item {item.item_id:24} {item.kind:8} {item.tags}\n")
    return EXIT_OK


def cmd_serve(args: argparse.Namespace, out, err) -> int:
    """Expose ``frames.duckdb`` read-only over Quack (§8.6) — feature-flagged.

    Quack is ``core_nightly`` until DuckDB v2.0 (PRD §3.4), so this refuses
    unless the flag is set, and says what to use instead. Nothing in CashKit
    depends on it: Parquet export is the stable sharing path.
    """
    import os

    if not args.quack:
        err.write("cashkit serve currently offers only --quack.\n")
        return EXIT_USAGE
    enabled = args.enable_experimental or os.environ.get(QUACK_FLAG_ENV) == "1"
    if not enabled:
        payload = {
            "started": False,
            "reason": (
                "Quack is experimental (core_nightly until DuckDB v2.0, PRD §3.4) "
                "and is disabled by default."
            ),
            "enable_with": f"{QUACK_FLAG_ENV}=1 or --enable-experimental",
            "stable_alternative": "export the frame to Parquet",
        }
        if args.json:
            _emit(out, payload)
        else:
            err.write(payload["reason"] + "\n")
            err.write(f"Enable with {payload['enable_with']}.\n")
            err.write(f"Stable alternative: {payload['stable_alternative']}.\n")
        return EXIT_DIAGNOSTIC

    frames = Path(args.path) / "frames.duckdb"
    started, reason = _start_quack(frames, args.host, args.port, args.token)
    payload = {
        "started": started,
        "database": str(frames),
        "host": args.host,
        "port": args.port,
        "reason": reason,
        "experimental": True,
    }
    if args.json:
        _emit(out, payload)
    elif started:  # pragma: no cover - requires a Quack-capable DuckDB build
        out.write(f"Serving {frames} read-only on {args.host}:{args.port} (Quack).\n")
    else:
        err.write(f"Could not start the Quack server: {reason}\n")
    return EXIT_OK if started else EXIT_DIAGNOSTIC


def _start_quack(database: Path, host: str, port: int, token: str) -> tuple[bool, str]:
    """Try to start Quack; report why not rather than raising.

    The CLI never imports the DuckDB driver: exposing a frame store over a wire
    is a *frame store* operation, and routing it through
    :meth:`~cashkit.stores.frames.DuckdbFrameStore.serve_quack` keeps the
    protocol the swappability guarantee it is meant to be (PRD §3.4). The import
    is local so ``cashkit doctor`` still runs with no extras installed.
    """
    if not database.is_file():
        return False, f"no frame store at {database}; materialize a run first"
    try:
        from cashkit.stores.frames import DuckdbFrameStore
    except ImportError:
        return False, "the 'duckdb' extra is not installed: pip install 'cashkit[duckdb]'"
    store = DuckdbFrameStore(database)
    try:
        return store.serve_quack(host=host, port=port, token=token)
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """The whole CLI surface (PRD §8.3, §8.4, §8.6). No diagnostics."""
    parser = argparse.ArgumentParser(
        prog="cashkit",
        description=(
            "Deterministic cash-flow modelling. Every command accepts --json and "
            "returns the same structure the SDK does."
        ),
    )
    parser.add_argument("--version", action="version", version=cashkit.__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str, handler) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, description=help_text)
        sub.add_argument("--json", action="store_true", help="machine-parseable output")
        sub.set_defaults(handler=handler)
        return sub

    init = add("init", "Create a book and its initial revision.", cmd_init)
    init.add_argument("path")
    init.add_argument("--id", default="", help="book id (defaults to the directory name)")
    init.add_argument("--grain", default="day", choices=[g.value for g in Grain])
    init.add_argument(
        "--horizon", required=True, type=_parse_horizon,
        metavar="START:END", help="YYYY-MM-DD:YYYY-MM-DD, end exclusive",
    )
    init.add_argument("--opening-balance", required=True, type=_parse_money)
    init.add_argument("--currency", default="EUR")
    init.add_argument("--calendar", default=None, help="country code seeding holidays, e.g. IT")
    init.add_argument("--fiscal-year-start", type=int, default=1, choices=range(1, 13))
    init.add_argument(
        "--cutover", type=date.fromisoformat, default=None,
        help="last reconciled boundary; defaults to the horizon start. Never today.",
    )
    init.add_argument("--message", default="initial commit")
    init.add_argument("--author", default="agent")
    init.add_argument("--no-commit", action="store_true", help="create the layout only")

    doctor = add("doctor", "Store connectivity, versions and git state.", cmd_doctor)
    doctor.add_argument("path", nargs="?", default=".")

    validate = add("validate", "Semantic diagnostics on the current book.", cmd_validate)
    validate.add_argument("path", nargs="?", default=".")
    validate.add_argument("--scenario", default="base")

    run = add("run", "Evaluate a scenario and print its summary.", cmd_run)
    run.add_argument("scenario", nargs="?", default="base")
    run.add_argument("--path", default=".")
    run.add_argument("--grain", default=None, choices=[g.value for g in Grain])
    run.add_argument("--summary", action="store_true", help="accepted for §8.4 parity")

    status = add("status", "Structured working-state diff.", cmd_status)
    status.add_argument("path", nargs="?", default=".")

    commit = add("commit", "Commit the working state.", cmd_commit)
    commit.add_argument("path", nargs="?", default=".")
    commit.add_argument("-m", "--message", required=True)
    commit.add_argument("--author", default="agent")

    history = add("history", "Revision list.", cmd_history)
    history.add_argument("item", nargs="?", default=None)
    history.add_argument("--path", default=".")
    history.add_argument("--scenario", default=None)
    history.add_argument("--field", default=None)
    history.add_argument("--limit", type=int, default=50)

    describe = add("describe", "Schema, items and query vocabulary.", cmd_describe)
    describe.add_argument("path", nargs="?", default=".")
    describe.add_argument("--scenario", default="base")
    describe.add_argument("--text", action="store_true", help="human summary instead of JSON")

    serve = add("serve", "Expose frames.duckdb read-only (experimental).", cmd_serve)
    serve.add_argument("path", nargs="?", default=".")
    serve.add_argument("--quack", action="store_true", required=False)
    serve.add_argument("--host", default="127.0.0.1", help="localhost by default")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--token", default="")
    serve.add_argument("--enable-experimental", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None, *, out=None, err=None) -> int:
    """Run one command. Returns the process exit code.

    ``out``/``err`` are injectable so the CLI is testable without capturing
    process streams. Exit codes: 0 fine, 1 the book has errors or the operation
    was refused, 2 the invocation itself was wrong.
    """
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    parser = build_parser()
    try:
        # argparse writes usage and errors straight to ``sys.stderr``; the
        # streams are injectable so the CLI is testable, so its output has to be
        # redirected rather than lost.
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:  # argparse already explained itself
        return int(exc.code or EXIT_USAGE)
    return args.handler(args, out, err)
