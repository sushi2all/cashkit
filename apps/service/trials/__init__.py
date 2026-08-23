"""Service-level trials (SPEC §10).

Two kinds, and the difference matters.

**Invariant trials run with a scripted model** and must hold when the model
misbehaves. T13 (no unproposed or stale mutation), T14 (a question turn never
writes), T17 (a correction leaves a scar) and T18 (the record-actual
discriminator) are properties of the service, not of the provider, so their
model — where they have one at all — writes on questions on purpose. They run
on every commit.

**Ported trials T01–T12 run the pinned model for real** (`google/gemini-3.7-flash`,
ADR-0028) and assert the final state of the book numerically. They are the
model-behaviour gate: any prompt change or model change reruns them. They are
marked ``live_model`` and excluded from a per-commit run (SPEC §10, D-MLP-35).

    uv run pytest apps/service/tests apps/service/trials -q   # everything but the model
    uv run pytest apps/service/trials -m live_model -q        # the model-behaviour gate

What each ported trial carries over from ``proto/TESTLOG.md``:

===== ==================================================================
T01   a whole base budget from one instruction; exclusive-end windows
T02   editing one line of an existing book, keeping the history
T03   the construct the book cannot express — refuse, never approximate
T04   a scenario fork and a lever on it; base does not move
T05   exporting the book the model built
T06   round-tripping our own export back into a book
T07   a messy human budget sheet, sections and one-offs and a mid-year rise
T08   payment terms: accrual apart from cash, net 45
T09   quarterly and weekly recurrences, counted in the calendar
T10   the whole user path, sign-in to saved forecast
T11   numeric Q&A, and the turn that must not write
T12   comparing two scenarios in words
===== ==================================================================

Two proto trials are ported in a narrowed form, deliberately. T06 and T07 paste
the table as text: the xlsx pipeline is S5's (SPEC §7, gate T16), and what S2
owns is the model-behaviour half (D-MLP-27). T10 walks the API path an
interface will drive: the browser end-to-end is S3's gate.
"""
