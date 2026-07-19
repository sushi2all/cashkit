# ADR-0006 — Ledger watermark stamped at commit(); live runs use the full ledger

**Date** 2026-07-19 · **Status** accepted

## Context

The watermark lives on git-tracked `book.yaml`, but the PRD never said when it updates. If `import_events` writes it, every CSV import dirties tracked config and pollutes review diffs. If it lags, it's unclear what a run between import and commit should see.

## Decision

- The watermark is stamped by `commit()`, never by `import_events`.
- A live run always uses the full ledger.
- Only `at(ref)` truncates the ledger, to the watermark recorded at that revision.
- Snapshots record `engine_version` and the watermark; exact historical reproduction is guaranteed at matching engine version, and an engine-version mismatch surfaces as a reported delta, never a silent failure (this also fixed the Phase 9 gate, which demanded exact reproduction unconditionally — impossible across a behavior-changing engine fix).

## Consequences

- Imports are invisible to git; commits capture (config, watermark, snapshots) as one consistent unit — matching "config diff and outcome diff in the same commit".
- The uncommitted window is well-defined: you see everything, reproducibility applies from the commit boundary.
