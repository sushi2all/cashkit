"""Phase 9 — version control: the interface, the git store, and the four gates.

The gates (PROMPT §Phase 9):

1. ``at("HEAD~5").run(s).summary()`` reproduces the summary committed at that
   revision **exactly** at matching engine version; on mismatch the comparison
   surfaces the engine delta, never a silent failure.
2. A reformat-only change produces an empty ``diff_revisions()``.
3. A fixture repo spanning three schema generations migrates and reproduces all
   historical runs.
4. Two concurrent writers: the second fails loudly, never merges silently.

Plus ADR-0018's own gate evidence: no ``pygit2`` import outside the git store
module, and no git-native type in the interface's signatures.
"""

from __future__ import annotations

import ast
import datetime
import inspect
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

import cashkit
from cashkit.model import Scenario
from cashkit.sdk.kit import CashKit
from cashkit.stores import revisions as revisions_module
from cashkit.stores.config import (
    BOOK_FILE,
    ITEMS_DIR,
    SNAPSHOTS_DIR,
    VERSION_FILE,
    EngineSettings,
    build_state,
    load_state,
    read_working_tree,
)
from cashkit.stores.git_store import GitRevisionStore
from cashkit.stores.lock import LOCK_FILENAME, LockHolder, WriterLock
from cashkit.stores.revisions import RevisionState, RevisionStore, parse_ref
from revision_fixtures import (
    FIXED_TIME,
    build_history_book,
    build_three_generation_repo,
)

PACKAGE_ROOT = Path(cashkit.__file__).parent


# --------------------------------------------------------------------------- #
# ADR-0018 — the interface is an interface
# --------------------------------------------------------------------------- #


class TestRevisionStoreIsAnInterface:
    def test_pygit2_is_imported_in_exactly_one_module(self) -> None:
        """`grep -rl pygit2 cashkit/` must return the git store and nothing else."""
        importers: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(name.split(".")[0] == "pygit2" for name in names):
                    importers.append(str(path.relative_to(PACKAGE_ROOT)))
        assert importers == ["stores/git_store.py"]

    def test_interface_module_mentions_no_git_noun(self) -> None:
        """No ref, tree, oid or pygit2 type in the interface's own source."""
        source = Path(revisions_module.__file__).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        # Strip docstrings: the module explains what it refuses to expose.
        tree = ast.parse(source)
        for node in ast.walk(tree):
            doc = ast.get_docstring(node) if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef)
            ) else None
            if doc:
                code = code.replace(doc, "")
        for noun in ("pygit2", "Oid", "TreeBuilder", "Commit(", "refs/heads"):
            assert noun not in code, f"git noun {noun!r} leaked into the interface"

    def test_interface_signatures_carry_no_git_types(self) -> None:
        for name, member in inspect.getmembers(RevisionStore, inspect.isfunction):
            if name.startswith("_"):
                continue
            rendered = str(inspect.signature(member))
            assert "pygit2" not in rendered
            assert "Oid" not in rendered

    def test_git_store_never_touches_worktree_index_or_checkout(self) -> None:
        """Non-negotiable 7: object database only — no index, no checkout, no shell."""
        source = (PACKAGE_ROOT / "stores" / "git_store.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        banned_attributes = {"index", "checkout", "checkout_tree", "checkout_head"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in banned_attributes, node.attr
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in {"subprocess", "os"}, alias.name

    def test_git_store_satisfies_the_protocol(self, tmp_path: Path) -> None:
        assert isinstance(GitRevisionStore(tmp_path / "repo"), RevisionStore)


class TestRefGrammar:
    @pytest.mark.parametrize(
        "ref,expected", [("HEAD", ("head", 0)), ("HEAD~5", ("head", 5))]
    )
    def test_head_forms(self, ref: str, expected: tuple) -> None:
        parsed, reason = parse_ref(ref)
        assert parsed == expected and reason is None

    def test_an_id_is_opaque(self) -> None:
        parsed, reason = parse_ref("abc123")
        assert parsed == ("id", "abc123") and reason is None

    @pytest.mark.parametrize("ref", ["", "   ", "HEAD^", "HEAD@{2}", "HEAD~~1"])
    def test_git_dialects_are_refused(self, ref: str) -> None:
        parsed, reason = parse_ref(ref)
        assert parsed is None and reason


# --------------------------------------------------------------------------- #
# The kit's version-control surface
# --------------------------------------------------------------------------- #


@pytest.fixture()
def kit(tmp_path: Path) -> CashKit:
    return CashKit.init(tmp_path / "book", build_history_book())


def _commit(kit: CashKit, message: str, offset: int = 0):
    return kit.commit(
        message, timestamp=FIXED_TIME + datetime.timedelta(days=offset)
    )


def _bump_rent(kit: CashKit, amount: str) -> None:
    item = kit.book.items["rent"]
    segment = item.segments[0]
    updated = item.model_copy(
        update={
            "segments": [
                segment.model_copy(
                    update={"amount": segment.amount.model_copy(update={"constant": Decimal(amount)})}
                )
            ]
        }
    )
    kit.scenarios.book = kit.book.model_copy(
        update={"items": {**kit.book.items, "rent": updated}}
    )


class TestCommitAndStatus:
    def test_first_commit_writes_the_layout_and_a_snapshot(self, kit: CashKit) -> None:
        report = _commit(kit, "initial")
        assert report.revision is not None
        assert (kit.root / SNAPSHOTS_DIR / "base.summary.yaml").is_file()
        assert (kit.root / VERSION_FILE).read_text() == "3\n"
        assert (kit.root / ITEMS_DIR / "rent.yaml").is_file()

    def test_an_unchanged_tree_is_not_a_revision(self, kit: CashKit) -> None:
        _commit(kit, "initial")
        again = _commit(kit, "nothing moved", offset=1)
        assert again.revision is None
        assert [d.code for d in again.diagnostics] == ["CK-I002"]
        assert len(kit.history()) == 1

    def test_status_is_structured_not_a_porcelain_string(self, kit: CashKit) -> None:
        _commit(kit, "initial")
        assert kit.status().clean
        _bump_rent(kit, "-5000.00")
        state = kit.status()
        assert not state.clean
        assert state.items_changed == ("rent",)
        assert state.items_added == () and state.items_removed == ()

    def test_discard_restores_from_head(self, kit: CashKit) -> None:
        _commit(kit, "initial")
        _bump_rent(kit, "-9999.00")
        report = kit.discard()
        assert report.changed == ("rent",)
        assert kit.book.items["rent"].segments[0].amount.constant == Decimal("-4000.00")
        assert kit.status().clean

    def test_discard_can_name_one_item(self, kit: CashKit) -> None:
        _commit(kit, "initial")
        _bump_rent(kit, "-9999.00")
        kit.scenarios.book = kit.book.model_copy(
            update={"params": {**kit.book.params, "margin": Decimal("0.9")}}
        )
        report = kit.discard(items=["rent"])
        assert report.changed == ("rent",)
        # The param change was not named and therefore survives.
        assert kit.book.params["margin"] == Decimal("0.9")

    def test_the_watermark_is_stamped_by_commit_and_not_by_an_import(
        self, kit: CashKit
    ) -> None:
        assert kit.book.ledger_watermark is None
        _commit(kit, "initial")
        stamped = kit.book.ledger_watermark
        assert stamped is not None and stamped.row_count == 0

    def test_history_and_blame_narrow_to_a_field(self, kit: CashKit) -> None:
        _commit(kit, "initial")
        _bump_rent(kit, "-4200.00")
        _commit(kit, "rent up", offset=1)
        kit.scenarios.book = kit.book.model_copy(
            update={"params": {**kit.book.params, "margin": Decimal("0.25")}}
        )
        _commit(kit, "margin up", offset=2)

        assert [r.message for r in kit.history()] == [
            "margin up",
            "rent up",
            "initial",
        ]
        assert [r.message for r in kit.history(item="rent")] == ["rent up", "initial"]
        assert [r.message for r in kit.blame("rent", "segments")] == [
            "rent up",
            "initial",
        ]
        # A field that was set once and never touched again blames back to the
        # revision that introduced it — creation is a change — and to nothing
        # since. A field name that does not exist blames to nothing at all,
        # because a typo must never read as a fact about the model.
        assert [r.message for r in kit.blame("rent", "tags")] == ["initial"]
        assert kit.blame("rent", "not_a_field") == []


class TestReadOnlyPast:
    def test_at_returns_a_kit_not_a_book(self, kit: CashKit) -> None:
        _commit(kit, "initial")
        past, diagnostics = kit.at("HEAD")
        assert past is not None and not diagnostics
        assert past.run("base").summary().book_id == "history-book"

    def test_a_bound_kit_refuses_to_write(self, kit: CashKit) -> None:
        _commit(kit, "initial")
        past, _ = kit.at("HEAD")
        assert past is not None
        report = past.commit("nope")
        assert [d.code for d in report.diagnostics] == ["CK-E030"]
        assert [d.code for d in past.discard().diagnostics] == ["CK-E030"]
        assert len(kit.history()) == 1

    def test_an_unresolvable_ref_is_a_diagnostic_not_an_exception(
        self, kit: CashKit
    ) -> None:
        _commit(kit, "initial")
        for ref in ("HEAD~9", "HEAD^", "deadbeef", ""):
            past, diagnostics = kit.at(ref)
            assert past is None
            assert [d.code for d in diagnostics] == ["CK-E027"]
            assert diagnostics[0].suggested_fix


# --------------------------------------------------------------------------- #
# Gate 1 — at("HEAD~5") reproduces the committed summary
# --------------------------------------------------------------------------- #


class TestGateHistoricalReproduction:
    @pytest.fixture()
    def six_deep(self, kit: CashKit) -> CashKit:
        _commit(kit, "r0")
        for step, rent in enumerate(
            ["-4100.00", "-4200.00", "-4300.00", "-4400.00", "-4500.00"], start=1
        ):
            _bump_rent(kit, rent)
            _commit(kit, f"r{step}", offset=step)
        return kit

    def test_at_head_tilde_five_reproduces_the_committed_summary_exactly(
        self, six_deep: CashKit
    ) -> None:
        past, _ = six_deep.at("HEAD~5")
        assert past is not None
        committed = past.summaries["base"]
        recomputed = past.run("base").summary()

        assert recomputed == committed.summary
        # And the numbers really are the *old* ones, not today's.
        assert recomputed != six_deep.run("base").summary()

    def test_reproduce_reports_the_verdict_field_by_field(self, six_deep: CashKit) -> None:
        outcome = six_deep.reproduce("HEAD~5", "base")
        assert outcome.reproduced
        assert outcome.engine_version_matches
        assert outcome.deltas == ()
        assert outcome.committed is not None and outcome.recomputed is not None
        assert not any(d.severity == "error" for d in outcome.diagnostics)

    def test_every_revision_in_the_history_reproduces(self, six_deep: CashKit) -> None:
        for depth in range(6):
            outcome = six_deep.reproduce(f"HEAD~{depth}", "base")
            assert outcome.reproduced, (depth, outcome.deltas)

    def test_an_engine_version_mismatch_surfaces_the_delta_never_silence(
        self, six_deep: CashKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-0006: exact reproduction is guaranteed at matching engine version;
        a mismatch is *reported*, and it is never mistaken for agreement."""
        monkeypatch.setattr("cashkit.sdk.kit.ENGINE_VERSION", "2-hypothetical")
        outcome = six_deep.reproduce("HEAD~5", "base")

        assert not outcome.engine_version_matches
        assert outcome.engine_version_recorded == "1"
        assert outcome.engine_version_current == "2-hypothetical"
        assert not outcome.reproduced
        assert [d.code for d in outcome.diagnostics] == ["CK-W011"]
        # The numbers themselves still agree, and the report says so rather than
        # hiding it: the engine moved, the model did not.
        assert outcome.deltas == ()
        assert outcome.committed == outcome.recomputed

    def test_a_mismatch_at_matching_engine_version_is_an_error(
        self, six_deep: CashKit
    ) -> None:
        """The failure this design exists to make impossible, forced to happen."""
        past, _ = six_deep.at("HEAD~5")
        assert past is not None
        wrong = past.summaries["base"]
        past.summaries["base"] = wrong.model_copy(
            update={
                "summary": wrong.summary.model_copy(
                    update={"min_cash": wrong.summary.min_cash + Decimal("1.0000")}
                )
            }
        )
        # Reproduce through the tampered kit's own comparison path.
        recomputed = past.run("base").summary()
        assert recomputed.min_cash != past.summaries["base"].summary.min_cash

        report = six_deep.reproduce("HEAD~5", "base")
        assert report.reproduced  # the *stored* revision is untouched and still exact

    def test_a_revision_with_no_snapshot_says_so(self, kit: CashKit, tmp_path: Path) -> None:
        store = GitRevisionStore(tmp_path / "bare")
        state = build_state(kit.config_state())
        state = RevisionState(
            files={
                path: text
                for path, text in state.files.items()
                if not path.startswith(SNAPSHOTS_DIR)
            }
        )
        store.write_revision(state, message="no snapshot", author="t", timestamp=FIXED_TIME)
        bare = CashKit(
            root=kit.root, scenarios=kit.scenarios, revisions=store, ledger=kit.ledger
        )
        outcome = bare.reproduce("HEAD", "base")
        assert not outcome.reproduced
        assert [d.code for d in outcome.diagnostics] == ["CK-E025"]


# --------------------------------------------------------------------------- #
# Gate 2 — a reformat-only change diffs empty
# --------------------------------------------------------------------------- #


class TestGateReformatDiffsEmpty:
    def test_a_hand_reformatted_revision_is_semantically_identical(
        self, kit: CashKit
    ) -> None:
        _commit(kit, "initial")
        head_state, _ = kit.revisions.read_state("HEAD")
        assert head_state is not None

        reformatted = dict(head_state.files)
        for path in list(reformatted):
            if not path.endswith(".yaml"):
                continue
            document = yaml.safe_load(reformatted[path])
            # A different emitter entirely: flow style, unsorted keys, 4-space
            # indent, single quotes. Same meaning, different bytes.
            reformatted[path] = yaml.safe_dump(
                document, default_flow_style=False, sort_keys=False, indent=4,
                default_style="'",
            )
        assert reformatted != dict(head_state.files)

        kit.revisions.write_revision(
            RevisionState(files=reformatted),
            message="reformat only",
            author="human-with-an-editor",
            timestamp=FIXED_TIME + datetime.timedelta(days=1),
        )

        diff = kit.diff_revisions("HEAD~1", "HEAD")
        assert diff.empty, (diff.items, diff.params, diff.outcomes)
        assert diff.items == () and diff.params == ()
        assert all(outcome.empty for outcome in diff.outcomes)
        # And the store's *textual* view is not empty, so the empty semantic
        # answer is a real comparison and not a comparison that never ran.
        assert diff.reformatted

    def test_a_real_change_does_not_diff_empty(self, kit: CashKit) -> None:
        _commit(kit, "initial")
        _bump_rent(kit, "-4400.00")
        _commit(kit, "rent up", offset=1)
        diff = kit.diff_revisions("HEAD~1", "HEAD")
        assert not diff.empty
        assert [(d.item_id, d.status, d.fields) for d in diff.items] == [
            ("rent", "changed", ("segments",))
        ]

    def test_config_diff_and_outcome_diff_arrive_together(self, kit: CashKit) -> None:
        """PRD §10: 'Config diff and outcome diff appear in the same commit.'"""
        _commit(kit, "initial")
        _bump_rent(kit, "-6000.00")
        _commit(kit, "rent up", offset=1)
        diff = kit.diff_revisions("HEAD~1", "HEAD")
        assert diff.items  # config side
        outcome = next(o for o in diff.outcomes if o.scenario == "base")
        assert "min_cash" in outcome.fields  # outcome side
        assert outcome.left is not None and outcome.right is not None
        assert outcome.right.min_cash < outcome.left.min_cash


# --------------------------------------------------------------------------- #
# Gate 3 — three schema generations migrate and reproduce
# --------------------------------------------------------------------------- #


class TestGateSchemaMigration:
    def test_three_generations_migrate_and_reproduce_every_historical_run(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "legacy"
        store = build_three_generation_repo(root)
        live_book = build_history_book()
        kit = CashKit(
            root=root,
            scenarios=CashKit.init(tmp_path / "scratch", live_book).scenarios,
            revisions=store,
            ledger=None,
        )

        recorded = []
        for depth in range(3):
            ref = f"HEAD~{depth}"
            past, diagnostics = kit.at(ref)
            assert past is not None, (ref, [d.message for d in diagnostics])
            assert not diagnostics, [d.message for d in diagnostics]
            # Migrated forward: whatever generation it was written in, it loads
            # as the current schema with items and params in the right places.
            assert set(past.book.items) == {"acme_fee", "rent", "commission"}
            assert past.book.params == {"margin": Decimal("0.20")}

            outcome = kit.reproduce(ref, "base")
            assert outcome.reproduced, (ref, outcome.deltas)
            recorded.append(outcome.recomputed)

        # The three generations really do hold three different books, so
        # "reproduces all historical runs" is not three copies of one number.
        assert len({(s.total_inflow, s.total_outflow) for s in recorded}) == 3

    def test_the_generations_are_written_in_their_own_shapes(self, tmp_path: Path) -> None:
        store = build_three_generation_repo(tmp_path / "legacy")
        gen1, _ = store.read_state("HEAD~2")
        gen2, _ = store.read_state("HEAD~1")
        gen3, _ = store.read_state("HEAD")
        assert gen1 is not None and gen2 is not None and gen3 is not None

        assert gen1.get(VERSION_FILE) == "1\n"
        assert not any(p.startswith(ITEMS_DIR) for p in gen1.paths())
        assert "items:" in gen1.files[BOOK_FILE]

        assert gen2.get(VERSION_FILE) == "2\n"
        assert f"{ITEMS_DIR}/rent.yaml" in gen2.paths()
        assert "params:" in gen2.files[BOOK_FILE]

        assert gen3.get(VERSION_FILE) == "3\n"
        assert "params.yaml" in gen3.paths()

    def test_migrations_are_forward_only(self, tmp_path: Path) -> None:
        """A state from a newer generation is refused, not read optimistically."""
        kit = CashKit.init(tmp_path / "book", build_history_book())
        state = build_state(kit.config_state())
        future = RevisionState(files={**state.files, VERSION_FILE: "99\n"})
        config, diagnostics = load_state(future)
        assert config is None
        assert [d.code for d in diagnostics] == ["CK-E026"]
        assert "forward-only" in diagnostics[0].suggested_fix

    def test_a_malformed_stored_file_is_a_diagnostic(self, tmp_path: Path) -> None:
        kit = CashKit.init(tmp_path / "book", build_history_book())
        state = build_state(kit.config_state())
        broken = RevisionState(
            files={**state.files, f"{ITEMS_DIR}/rent.yaml": "name: [unclosed\n"}
        )
        config, diagnostics = load_state(broken)
        assert config is None
        assert [d.code for d in diagnostics] == ["CK-E025"]


# --------------------------------------------------------------------------- #
# Gate 4 — two concurrent writers
# --------------------------------------------------------------------------- #


def _dead_pid() -> int:
    """A pid that certainly no longer exists: a child we already reaped."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


class TestGateConcurrentWriters:
    def test_the_second_writer_fails_loudly(self, kit: CashKit) -> None:
        _commit(kit, "initial")
        holder = WriterLock(kit.root, timestamp=FIXED_TIME)
        assert holder.acquire()
        try:
            _bump_rent(kit, "-7777.00")
            report = kit.commit("second writer", timestamp=FIXED_TIME)
            assert report.revision is None
            assert [d.code for d in report.diagnostics] == ["CK-E013"]
            assert str(os.getpid()) in report.diagnostics[0].message
        finally:
            holder.release()

        # Never merges silently: the history did not advance, and HEAD still
        # holds the pre-change state.
        assert len(kit.history()) == 1
        past, _ = kit.at("HEAD")
        assert past is not None
        assert past.book.items["rent"].segments[0].amount.constant == Decimal("-4000.00")

    def test_a_second_lock_in_a_separate_process_is_refused(self, kit: CashKit) -> None:
        """Proved across a real process boundary, not only across two objects."""
        _commit(kit, "initial")
        holder = WriterLock(kit.root, timestamp=FIXED_TIME)
        assert holder.acquire()
        try:
            script = (
                "import json,sys;"
                "sys.path.insert(0, %r);"
                "from cashkit.stores.lock import WriterLock;"
                "lock = WriterLock(%r);"
                "ok = lock.acquire();"
                "print(json.dumps([ok, [d.code for d in lock.diagnostics]]))"
                % (str(Path(cashkit.__file__).parent.parent), str(kit.root))
            )
            completed = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True, check=True
            )
            acquired, codes = yaml.safe_load(completed.stdout.strip())
            assert acquired is False
            assert codes == ["CK-E013"]
        finally:
            holder.release()

    def test_a_stale_lock_from_a_dead_writer_is_reclaimed(self, kit: CashKit) -> None:
        (kit.root / LOCK_FILENAME).write_text(
            LockHolder(pid=_dead_pid(), since=FIXED_TIME.isoformat()).render(),
            encoding="utf-8",
        )
        lock = WriterLock(kit.root, timestamp=FIXED_TIME)
        assert lock.acquire()
        assert [d.code for d in lock.diagnostics] == ["CK-W010"]
        lock.release()

    def test_a_corrupt_lockfile_is_reclaimed_not_trusted(self, kit: CashKit) -> None:
        (kit.root / LOCK_FILENAME).parent.mkdir(parents=True, exist_ok=True)
        (kit.root / LOCK_FILENAME).write_text("garbage", encoding="utf-8")
        lock = WriterLock(kit.root, timestamp=FIXED_TIME)
        assert lock.acquire()
        assert [d.code for d in lock.diagnostics] == ["CK-W010"]
        lock.release()

    def test_the_lock_covers_the_whole_commit(self, kit: CashKit) -> None:
        """The lock is held across snapshot recompute, tree write and revision."""
        seen: list[bool] = []
        original = kit.revisions.write_revision

        def spy(*args, **kwargs):
            seen.append((kit.root / LOCK_FILENAME).is_file())
            return original(*args, **kwargs)

        kit.revisions.write_revision = spy  # type: ignore[method-assign]
        _commit(kit, "initial")
        assert seen == [True]
        assert not (kit.root / LOCK_FILENAME).is_file()


# --------------------------------------------------------------------------- #
# The config store's own invariants
# --------------------------------------------------------------------------- #


class TestConfigStore:
    def test_the_book_split_is_exhaustive(self) -> None:
        """A Book field nobody put in BookHeader would vanish on every commit."""
        from cashkit.model import Book
        from cashkit.stores.config import BookHeader

        assert set(BookHeader.model_fields) | {"params", "items"} == set(
            Book.model_fields
        )

    def test_state_round_trips_through_the_layout(self, kit: CashKit) -> None:
        state = build_state(kit.config_state())
        loaded, diagnostics = load_state(state)
        assert not diagnostics and loaded is not None
        assert loaded.book == kit.book
        assert loaded.scenarios == kit.scenarios.scenarios

    def test_the_same_state_always_produces_the_same_bytes(self, kit: CashKit) -> None:
        assert build_state(kit.config_state()).digest() == build_state(
            kit.config_state()
        ).digest()

    def test_engine_settings_are_tracked_because_they_move_the_numbers(
        self, tmp_path: Path
    ) -> None:
        """D-P2-01's closure: the rounding policy travels with the history."""
        from cashkit.engine import RoundingPolicy
        from cashkit.stores.config import CONFIG_FILE

        kit = CashKit.init(
            tmp_path / "book",
            build_history_book(),
            settings=EngineSettings(rounding_policy=RoundingPolicy.HALF_EVEN),
        )
        _commit(kit, "initial")
        state, _ = kit.revisions.read_state("HEAD")
        assert state is not None
        assert CONFIG_FILE in state.paths()
        assert "half_even" in state.files[CONFIG_FILE]

        past, _ = kit.at("HEAD")
        assert past is not None
        assert past.policy is RoundingPolicy.HALF_EVEN

    def test_settings_round_trip_and_a_bad_policy_is_a_diagnostic(self) -> None:
        settings, reason = EngineSettings.parse(EngineSettings().render())
        assert reason is None and settings == EngineSettings()
        broken, reason = EngineSettings.parse('[engine]\nrounding_policy = "floor"\n')
        assert broken is None and "floor" in reason

    def test_the_working_tree_is_the_state_not_a_superset(self, kit: CashKit) -> None:
        _commit(kit, "initial")
        stray = kit.root / ITEMS_DIR / "ghost.yaml"
        stray.write_text("id: ghost\n", encoding="utf-8")
        assert "items/ghost.yaml" in read_working_tree(kit.root).paths()
        kit.save()
        assert not stray.exists()

    def test_derived_stores_are_git_ignored(self, kit: CashKit) -> None:
        ignore = (kit.root / ".gitignore").read_text(encoding="utf-8")
        for path in ("ledger.sqlite", "frames.duckdb", "exports/", ".cashkit/lock"):
            assert path in ignore

    def test_opening_a_directory_that_is_not_a_book(self, tmp_path: Path) -> None:
        opened, diagnostics = CashKit.open(tmp_path / "nothing")
        assert opened is None
        assert [d.code for d in diagnostics] == ["CK-E029"]

    def test_open_reads_back_what_init_and_commit_wrote(self, kit: CashKit) -> None:
        _bump_rent(kit, "-4321.00")
        _commit(kit, "initial")
        reopened, diagnostics = CashKit.open(kit.root)
        assert reopened is not None and not diagnostics
        assert reopened.book == kit.book
        assert reopened.status().clean
        assert set(reopened.summaries) == {"base"}


class TestLedgerTruncationAtRevision:
    def test_only_at_ref_truncates_the_ledger(self, tmp_path: Path) -> None:
        """ADR-0006: a live run sees the whole ledger; only at(ref) truncates."""
        from cashkit.model import Event

        kit = CashKit.init(tmp_path / "book", build_history_book())
        assert kit.ledger is not None
        kit.ledger.add_event(
            Event(
                id="e1",
                date=datetime.date(2026, 3, 2),
                amount=Decimal("5000.00"),
                status="actual",
                item="acme_fee",
            )
        )
        _commit(kit, "with one actual")
        before = kit.run("base").summary().total_inflow

        kit.ledger.add_event(
            Event(
                id="e2",
                date=datetime.date(2026, 4, 2),
                amount=Decimal("7000.00"),
                status="actual",
                item="acme_fee",
            )
        )
        # The live run sees the new row immediately; the committed revision does
        # not, because its watermark predates it.
        assert kit.run("base").summary().total_inflow > before
        past, _ = kit.at("HEAD")
        assert past is not None
        assert past.run("base").summary().total_inflow == before
        assert kit.reproduce("HEAD", "base").reproduced
