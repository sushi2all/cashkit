/**
 * GENERATED FILE — DO NOT EDIT.
 *
 * Source: apps/service/openapi.json
 * Regenerate: npm run api:generate
 * Verify:     npm run api:check-drift
 *
 * Hand-editing this file is an explicit anti-pattern of the MLP track
 * (PROMPT §Anti-patterns). Change the service's Pydantic models, republish the
 * schema with `uv run python -m cashkit_service.openapi`, then regenerate.
 */
/* eslint-disable */

export interface paths {
    "/auth/link": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Request Link
         * @description Send a magic link.
         *
         *     The answer is the same whether or not the address has an account: an
         *     endpoint that distinguishes them is an account-enumeration oracle.
         */
        post: operations["request_link_auth_link_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Verify Link
         * @description Exchange a single-use link token for a bearer session.
         */
        post: operations["verify_link_auth_verify_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/compare": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Compare Scenarios */
        get: operations["compare_scenarios_book_compare_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/discard": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Discard Working Overlay
         * @description Reload the working overlay from HEAD (SPEC §2.4).
         *
         *     The ledger is untouched: it is append-only and shared by every scenario, so
         *     "discard my uncommitted plan changes" never un-records something that
         *     happened (ADR-0012).
         */
        post: operations["discard_working_overlay_book_discard_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/edits": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Edit Proposal */
        post: operations["create_edit_proposal_book_edits_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Events
         * @description ``GET /book/events`` — the ledger view (F5).
         *
         *     ``include_voided`` is what makes a correction's scar visible: the original
         *     row is tombstoned, not deleted, and the Actuals screen shows it struck with
         *     the correction linked (ADR-0012, SPEC §6-S7).
         */
        get: operations["get_events_book_events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/forecast": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Forecast
         * @description ``GET /book/forecast`` — the grid payload for F3.
         *
         *     IN and OUT are the split of the same cash columns the closing series is
         *     built from, summed per period. Nothing is re-derived from a rounded figure:
         *     the addition happens on the engine's int64 minor units and is serialized
         *     once, at the end.
         */
        get: operations["get_forecast_book_forecast_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/history": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get History
         * @description ``GET /book/history`` — R12, the read-only revision list (SPEC §6-S15).
         */
        get: operations["get_history_book_history_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/reconcile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Reconcile
         * @description ``GET /book/reconcile`` — per-item forecast/actual/drift (F5, S8).
         *
         *     ``until`` defaults to ``as_of``: the host fills the date, the engine never
         *     reads a clock to find it (ADR-0019 rule 2).
         */
        get: operations["get_reconcile_book_reconcile_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/save": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Save Book
         * @description M9 — commit the working overlay (SPEC §2.4).
         *
         *     Committing is not a change to the overlay, so it needs no proposal of its
         *     own: it records changes the user already confirmed one card at a time. It
         *     does move the revision, so every pending card is superseded (SPEC §2.5).
         */
        post: operations["save_book_book_save_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/scenarios": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Scenarios */
        get: operations["list_scenarios_book_scenarios_get"];
        put?: never;
        /**
         * Create Scenario
         * @description Create a fork — as a proposal, like every other write.
         *
         *     SPEC §5-F4 has fork creation as "M7 via turn or button". The button path is
         *     this endpoint, and it produces a confirmation card rather than a scenario:
         *     ADR-0029 admits no exception for a change that merely looks harmless
         *     (D-MLP-14).
         */
        post: operations["create_scenario_book_scenarios_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/scenarios/{scenario_id}/activate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Activate Scenario
         * @description Switch the working context, book-wide, from the next request on.
         *
         *     Activation invalidates every pending proposal: a card dry-run against one
         *     scenario must never be applied to another (SPEC §2.5).
         */
        post: operations["activate_scenario_book_scenarios__scenario_id__activate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/state": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get State */
        get: operations["get_state_book_state_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/trace": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Trace
         * @description ``GET /book/trace`` — ``trace()`` for the tap-to-explain screen (R7).
         */
        get: operations["get_trace_book_trace_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/validate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Validate
         * @description ``GET /book/validate`` — R10.
         *
         *     ``validate()`` checks model consistency, not domain completeness
         *     (ADR-0021); the consumer MLP defers the domain-coverage duty entirely
         *     (D-MLP-02). The diagnostics render verbatim, with no advice framing.
         */
        get: operations["get_validate_book_validate_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/book/why_zero": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Why Zero
         * @description ``GET /book/why_zero`` — R8.
         *
         *     The cause and the suggested fix travel verbatim; the service never
         *     paraphrases an engine explanation into advice (ADR-0015).
         */
        get: operations["get_why_zero_book_why_zero_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/books": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Create Book Endpoint
         * @description Create the account's single book.
         *
         *     One book per user is structural: ``books.user_id`` is UNIQUE (SPEC §4), so
         *     a second attempt is refused by the database, not by a check that could be
         *     raced.
         */
        post: operations["create_book_endpoint_books_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Export Workbook
         * @description Export the book as xlsx.
         *
         *     A spreadsheet cell is a float — that is Excel's type, not a choice this
         *     service makes. The conversion happens exactly once, at the cell boundary,
         *     on a Decimal the engine produced; no arithmetic is ever done on the result
         *     (D-MLP-13). Anyone who needs the exact figure has the API, whose money is
         *     always a Decimal string.
         */
        get: operations["export_workbook_export_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/import": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Start Import
         * @description Start an import. It applies nothing; it produces a card and a report.
         */
        post: operations["start_import_import_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/imports/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Read Import
         * @description The terminal payload, for a client that cannot read a stream.
         *
         *     The stream is the primary surface and it replays, so this adds no
         *     capability the stream lacks; what it adds is a plain JSON reading of the
         *     same payload for a platform without streaming ``fetch`` (D-MLP-77). It is
         *     a read: it starts nothing and it changes nothing.
         */
        get: operations["read_import_imports__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/imports/{job_id}/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Stream Import
         * @description Server-sent progress for one import (SPEC §3, §6-S14).
         *
         *     Everything already emitted is replayed before the stream waits, so a
         *     listener that arrives late — or reconnects — sees the whole run.
         */
        get: operations["stream_import_imports__job_id__stream_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Me */
        get: operations["get_me_me_get"];
        put?: never;
        post?: never;
        /**
         * Delete Me
         * @description Full account deletion (SPEC §9).
         *
         *     Four things go, and the fourth is the one a cascade cannot reach.
         *
         *     1. **Every session**, so no device keeps reading.
         *     2. **The book directory**, off the volume, immediately.
         *     3. **Every Postgres row for the account** — ``turns``, ``llm_calls``,
         *        ``proposals`` and ``import_jobs`` included, by cascade from ``users``,
         *        which is the root of all of them. The row itself goes too, rather than
         *        gaining a ``deleted_at``: keeping it would keep the email for ever and
         *        would block the address from ever signing up again (D-MLP-22).
         *     4. **Every magic-link token issued to the address.** ``login_tokens`` has
         *        no ``user_id`` — a link can be requested before an account exists — so
         *        no cascade reaches it, and an unconsumed row would leave the address in
         *        the database after the account that owned it was erased. It is deleted
         *        by address, which also burns any link already in flight.
         *
         *     Backups cannot be immediate: an object already in the bucket holds the
         *     account until it ages out. SPEC §9 gives that thirty days, and the
         *     ``deletions`` receipt is what carries the obligation once the row that
         *     owed it is gone. It holds no personal data — a uuid that now references
         *     nothing, and two timestamps. :mod:`cashkit_service.retention` closes it
         *     against what the bucket actually still holds, not against elapsed time.
         */
        delete: operations["delete_me_me_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/me/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Export Me
         * @description Everything the user owns, one archive (GDPR, SPEC §3/§9).
         *
         *     Postgres rows plus the book directory itself — the YAML revisions and the
         *     ledger are the user's data, not an internal format they are locked out of.
         *     Session and link token hashes are excluded: they are credentials, and
         *     exporting them would widen the blast radius of a leaked archive.
         */
        get: operations["export_me_me_export_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/proposals/{proposal_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Resolve Proposal
         * @description ADR-0029's confirmation step.
         *
         *     Accept re-checks the §2.5 staleness fingerprint first. On a mismatch it does
         *     NOT apply: it re-runs the dry-run against the book as it is now and hands
         *     back a refreshed proposal. Applying blind is the failure this check exists
         *     to prevent.
         */
        post: operations["resolve_proposal_proposals__proposal_id__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/turns": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Turn */
        post: operations["create_turn_turns_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * AcceptResponse
         * @description The answer to a confirmation.
         *
         *     ``applied`` when the change landed, ``refreshed`` when the ground had moved
         *     and the service re-ran the dry-run — the old card is superseded and the new
         *     one needs confirming again (SPEC §2.5). ``discarded`` speaks for itself.
         */
        AcceptResponse: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /**
             * Diagnostics
             * @default []
             */
            diagnostics: unknown[];
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "applied" | "refreshed" | "discarded";
            proposal: components["schemas"]["ProposalOut"];
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Scenario */
            scenario: string;
            /**
             * Superseded
             * @default []
             */
            superseded: string[];
            what_if: components["schemas"]["WhatIf"];
        };
        /** ActivateResponse */
        ActivateResponse: {
            /** Active */
            active: string;
            /** Superseded Proposals */
            superseded_proposals: string[];
        };
        /**
         * AddEvent
         * @description M5 — ``add_event``.
         *
         *     Status is NOT a slot. The model never chooses whether something happened:
         *     the record-actual discriminator decides it host-side (SPEC §5-F5).
         */
        AddEvent: {
            /**
             * Amount
             * @example -912.50
             */
            amount: string;
            /** Date */
            date?: string | null;
            /** Direction */
            direction?: ("in" | "out") | null;
            /** Id */
            id?: string | null;
            /** Item */
            item?: string | null;
            /** Note */
            note?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "add_event";
            /** Scenario */
            scenario?: string | null;
        };
        /**
         * AddItem
         * @description M1 — ``add_item``.
         */
        AddItem: {
            /**
             * Amount
             * @example -912.50
             */
            amount: string;
            /**
             * Direction
             * @enum {string}
             */
            direction: "in" | "out";
            /** End */
            end?: string | null;
            /** Id */
            id: string;
            /** Name */
            name?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "add_item";
            /**
             * Recurrence
             * @default 1m
             * @example 1m
             * @example 3m
             * @example 1y
             */
            recurrence: string;
            /** Scenario */
            scenario?: string | null;
            /**
             * Settlement
             * @example immediate
             * @example net30
             */
            settlement?: string | null;
            /**
             * Start
             * Format: date
             */
            start: string;
            /** Tags */
            tags?: {
                [key: string]: string;
            };
        };
        /** BindingOut */
        BindingOut: {
            /** Detail */
            detail: string;
            /** Kind */
            kind: string;
            /** Source */
            source: string;
            /** Symbol */
            symbol: string;
            /** Target */
            target: string;
            value: components["schemas"]["Money"];
        };
        /** Body_start_import_import_post */
        Body_start_import_import_post: {
            /**
             * File
             * @description An .xlsx workbook.
             */
            file: string;
        };
        /** BookCreated */
        BookCreated: {
            /** Active Scenario */
            active_scenario: string;
            /** Book Id */
            book_id: string;
            /** Diagnostics */
            diagnostics: components["schemas"]["DiagnosticOut"][];
            /** Revision */
            revision: string | null;
        };
        /** BookParams */
        BookParams: {
            /** Currency */
            currency: string;
            /**
             * Cutover
             * Format: date
             */
            cutover: string;
            /** Grain */
            grain: string;
            /**
             * Horizon End
             * Format: date
             */
            horizon_end: string;
            /**
             * Horizon Start
             * Format: date
             */
            horizon_start: string;
            /** Id */
            id: string;
            opening_balance: components["schemas"]["Money"];
            /** Params */
            params: {
                [key: string]: string;
            };
        };
        /**
         * BookState
         * @description ``GET /book/state`` — SPEC §3.
         *
         *     Items, params, summary, months, per-item series, dirty flag, revision id,
         *     as_of, and server-computed ``warnings``.
         */
        BookState: {
            /** Active Scenario */
            active_scenario: string;
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            book: components["schemas"]["BookParams"];
            /** Closing */
            closing: components["schemas"]["Money"][];
            /** Diagnostics */
            diagnostics: unknown[];
            /** Dirty */
            dirty: boolean;
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            /** Items */
            items: components["schemas"]["ItemSeries"][];
            /** Months */
            months: string[];
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Scenario */
            scenario: string;
            /** Scenarios */
            scenarios: string[];
            summary: components["schemas"]["SummaryOut"];
            warnings: components["schemas"]["Warnings"];
            what_if: components["schemas"]["WhatIf"];
        };
        /**
         * CheckResult
         * @description One row of the reconciliation report (SPEC §6-S14).
         *
         *     ``sheet_value`` and ``delta`` are plain decimal strings on purpose. Only
         *     ``engine_value`` is a money figure, because only it is one: the other two
         *     are a spreadsheet cell and a comparison between the two systems.
         */
        CheckResult: {
            /**
             * Basis
             * @default absolute
             * @enum {string}
             */
            basis: "absolute" | "added";
            /** Delta */
            delta?: string | null;
            engine_value?: components["schemas"]["Money"] | null;
            /** Label */
            label: string;
            /** Measure */
            measure: string;
            /**
             * Note
             * @default
             */
            note: string;
            /**
             * Parity
             * @default false
             */
            parity: boolean;
            /** Period */
            period?: string | null;
            /** Ref */
            ref: string;
            /** Sheet Value */
            sheet_value?: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "matched" | "mismatched" | "skipped";
        };
        /**
         * ComparePeriod
         * @description One period of a scenario comparison.
         *
         *     ``values`` maps scenario id to figure, and a scenario absent from a period
         *     is ``null`` — never ``0``. The engine keeps that distinction and so does
         *     this payload (SPEC §5-F4).
         */
        ComparePeriod: {
            delta?: components["schemas"]["Money"] | null;
            /**
             * Period Start
             * Format: date
             */
            period_start: string;
            /** Values */
            values: {
                [key: string]: components["schemas"]["Money"] | null;
            };
        };
        /**
         * CompareResponse
         * @description ``GET /book/compare`` — the R9 payload.
         *
         *     A scenario absent from a period is ``null``, never ``0``; the engine keeps
         *     absent and zero apart and so does this payload (SPEC §5-F4).
         */
        CompareResponse: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /** Diagnostics */
            diagnostics: unknown[];
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            /** Metric */
            metric: string;
            /** Periods */
            periods: components["schemas"]["ComparePeriod"][];
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Scenario */
            scenario: string;
            /** Scenarios */
            scenarios: string[];
            what_if: components["schemas"]["WhatIf"];
        };
        /**
         * CorrectActual
         * @description M6 — ``correct_actual``. The note is mandatory (ADR-0012).
         */
        CorrectActual: {
            /**
             * Amount
             * @example -912.50
             */
            amount: string;
            /** Date */
            date?: string | null;
            /** Event */
            event: string;
            /** Note */
            note: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "correct_actual";
            /** Scenario */
            scenario?: string | null;
        };
        /**
         * CreateBook
         * @description Onboarding step (a): horizon and opening balance, nothing else.
         *
         *     Currency is EUR and grain is month for the whole MLP (SPEC §1, §3); they
         *     are fixed here rather than offered, because an option nothing supports is a
         *     way to build an unusable book.
         */
        CreateBook: {
            /**
             * Calendar
             * @example IT
             */
            calendar?: string | null;
            /**
             * Currency
             * @default EUR
             * @constant
             */
            currency: "EUR";
            /** Cutover */
            cutover?: string | null;
            /**
             * Grain
             * @default month
             * @constant
             */
            grain: "month";
            /**
             * Horizon End
             * Format: date
             */
            horizon_end: string;
            /**
             * Horizon Start
             * Format: date
             */
            horizon_start: string;
            /**
             * Opening Balance
             * @example 2500.00
             */
            opening_balance: string;
        };
        /** CreateScenario */
        CreateScenario: {
            /** Name */
            name: string;
            /**
             * Note
             * @default
             */
            note: string;
            /** Parent */
            parent?: string | null;
        };
        /**
         * Crossing
         * @description A month this change turns negative (D-MLP-05(b)).
         */
        Crossing: {
            after: components["schemas"]["Money"];
            before: components["schemas"]["Money"];
            /**
             * Period
             * Format: date
             */
            period: string;
        };
        /**
         * Deltas
         * @description The proposal card's deltas block.
         */
        Deltas: {
            /** Affected Events */
            affected_events: string[];
            /** Affected Items */
            affected_items: string[];
            closing_balance: components["schemas"]["MoneyMove"];
            /** Crossings */
            crossings: components["schemas"]["Crossing"][];
            min_cash: components["schemas"]["MoneyMove"];
            min_cash_period: components["schemas"]["PeriodMove"];
            /** Negative Months After */
            negative_months_after: number;
            /** Negative Months Before */
            negative_months_before: number;
            runway_end: components["schemas"]["PeriodMove"];
            /** Runway Periods After */
            runway_periods_after: number | null;
            /** Runway Periods Before */
            runway_periods_before: number | null;
        };
        /**
         * DiagnosticOut
         * @description One engine diagnostic, verbatim.
         */
        DiagnosticOut: {
            /** Code */
            code: string;
            /** Field */
            field?: string | null;
            /** Item Id */
            item_id?: string | null;
            /** Message */
            message: string;
            /** Severity */
            severity: string;
            /** Suggested Fix */
            suggested_fix: string;
        };
        /** DiscardResponse */
        DiscardResponse: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /**
             * Diagnostics
             * @default []
             */
            diagnostics: unknown[];
            /** Discarded */
            discarded: boolean;
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Scenario */
            scenario: string;
            /**
             * Superseded
             * @default []
             */
            superseded: string[];
            what_if: components["schemas"]["WhatIf"];
        };
        /**
         * EditScheduleDate
         * @description Host op — add, change or remove one explicit date on a schedule item.
         */
        EditScheduleDate: {
            /**
             * Action
             * @enum {string}
             */
            action: "add" | "change" | "remove";
            /** Amount */
            amount?: string | null;
            /**
             * Date
             * Format: date
             */
            date: string;
            /** Item */
            item: string;
            /** New Date */
            new_date?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "edit_schedule_date";
            /** Scenario */
            scenario?: string | null;
        };
        /**
         * EditsRequest
         * @description ``POST /book/edits`` — UI-origin proposals, no model call.
         *
         *     ``context`` carries the record-actual channel marker. It is the same field
         *     ``POST /turns`` carries, and it feeds the same discriminator, so the rule of
         *     SPEC §5-F5 has exactly one implementation.
         */
        EditsRequest: {
            /** Context */
            context?: "actuals_record" | null;
            /** Ops */
            ops: (components["schemas"]["AddItem"] | components["schemas"]["SetAmount"] | components["schemas"]["ShiftItems"] | components["schemas"]["ScaleItems"] | components["schemas"]["AddEvent"] | components["schemas"]["CorrectActual"] | components["schemas"]["ForkScenario"] | components["schemas"]["SetCutover"] | components["schemas"]["Save"] | components["schemas"]["SetHorizon"] | components["schemas"]["SetOpeningBalance"] | components["schemas"]["RemoveEvent"] | components["schemas"]["EditScheduleDate"] | components["schemas"]["RecordActual"])[];
            /**
             * Origin
             * @default cell_edit
             * @enum {string}
             */
            origin: "turn" | "cell_edit" | "onboarding" | "import" | "settings" | "button";
            /** Scenario */
            scenario?: string | null;
        };
        /**
         * EventOut
         * @description One ledger row, as ``query_events`` produced it.
         */
        EventOut: {
            amount: components["schemas"]["Money"];
            /** Corrects */
            corrects: string | null;
            /** Currency */
            currency: string;
            /**
             * Date
             * Format: date
             */
            date: string;
            /** Ext Id */
            ext_id: string | null;
            /** Id */
            id: string;
            /** Item */
            item: string | null;
            /** Note */
            note: string | null;
            /** Source */
            source: string | null;
            /** Status */
            status: string;
            /** Tags */
            tags: {
                [key: string]: string;
            };
        };
        /** EventsResponse */
        EventsResponse: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            /** Events */
            events: components["schemas"]["EventOut"][];
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Scenario */
            scenario: string;
            what_if: components["schemas"]["WhatIf"];
        };
        /** ExplanationOut */
        ExplanationOut: {
            /** Also */
            also: string[];
            /** Cause */
            cause: string;
            /** Detail */
            detail: string;
            /** Diagnostics */
            diagnostics: components["schemas"]["DiagnosticOut"][];
            /** Item Id */
            item_id: string;
            /** Measure */
            measure: string;
            /** Message */
            message: string;
            /** Period Index */
            period_index: number;
            /**
             * Period Start
             * Format: date
             */
            period_start: string;
            /** Suggested Fix */
            suggested_fix: string;
            value: components["schemas"]["Money"];
        };
        /** Forecast */
        Forecast: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /** Diagnostics */
            diagnostics: unknown[];
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            /** Grain */
            grain: string;
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Rows */
            rows: components["schemas"]["ForecastRow"][];
            /** Scenario */
            scenario: string;
            summary: components["schemas"]["SummaryOut"];
            warnings: components["schemas"]["Warnings"];
            what_if: components["schemas"]["WhatIf"];
            /** Window */
            window: string[];
        };
        /**
         * ForecastRow
         * @description One row of the designed monthly view: MONTH / IN / OUT / END.
         */
        ForecastRow: {
            closing: components["schemas"]["Money"];
            inflow: components["schemas"]["Money"];
            net: components["schemas"]["Money"];
            outflow: components["schemas"]["Money"];
            /**
             * Period
             * Format: date
             */
            period: string;
        };
        /**
         * ForkScenario
         * @description M7 — ``fork_scenario``.
         */
        ForkScenario: {
            /** Name */
            name: string;
            /**
             * Note
             * @default
             */
            note: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "fork_scenario";
            /** Parent */
            parent?: string | null;
            /** Scenario */
            scenario?: string | null;
        };
        /** HistoryResponse */
        HistoryResponse: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Revisions */
            revisions: components["schemas"]["RevisionOut"][];
            /** Scenario */
            scenario: string;
            what_if: components["schemas"]["WhatIf"];
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * ImportDone
         * @description The terminal payload: the report, the one card, and what went wrong.
         *
         *     The figures in the report are dry-run figures for the target scenario, so
         *     the envelope's ``what_if`` is stamped — SPEC §2.4 admits no exception for a
         *     number that came out of an import.
         */
        ImportDone: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /** Diagnostics */
            diagnostics?: components["schemas"]["DiagnosticOut"][];
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            /** Error */
            error?: string | null;
            /** Job Id */
            job_id: string;
            /**
             * Kind
             * @default done
             */
            kind: string;
            proposal?: components["schemas"]["ProposalOut"] | null;
            report: components["schemas"]["ReconciliationReport"];
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Scenario */
            scenario: string;
            /** Status */
            status: string;
            what_if: components["schemas"]["WhatIf"];
        };
        /**
         * ImportStarted
         * @description The answer to ``POST /import``.
         *
         *     It carries no computed figure — the job has not run yet — so it carries no
         *     provenance envelope either. The figures arrive on the stream, stamped.
         */
        ImportStarted: {
            /** Call Cap */
            call_cap: number;
            /** Job Id */
            job_id: string;
            /**
             * Kind
             * @default started
             */
            kind: string;
            /**
             * Reply
             * @default
             */
            reply: string;
            /** Retry After Seconds */
            retry_after_seconds?: number | null;
            /** Status */
            status: string;
            /** Stream */
            stream: string;
            target: components["schemas"]["ImportTarget"];
        };
        /**
         * ImportTarget
         * @description Where an import lands, and why (SPEC §7.3).
         */
        ImportTarget: {
            /** Created Fork */
            created_fork: boolean;
            /** Message */
            message: string;
            /** Reason */
            reason: string;
            /** Scenario */
            scenario: string;
        };
        /**
         * ItemSeries
         * @description One item's columns over the horizon.
         */
        ItemSeries: {
            /** Accrual */
            accrual: components["schemas"]["Money"][];
            /** Cash */
            cash: components["schemas"]["Money"][];
            /** Direction */
            direction: string | null;
            /** Formula */
            formula: string | null;
            /** Id */
            id: string;
            /** Kind */
            kind: string;
            /** Name */
            name: string;
            /** Tags */
            tags: {
                [key: string]: string;
            };
        };
        /** LinkRequest */
        LinkRequest: {
            /**
             * Email
             * Format: email
             */
            email: string;
            /**
             * Platform
             * @default web
             * @enum {string}
             */
            platform: "web" | "mobile";
        };
        /** Me */
        Me: {
            /** Active Scenario */
            active_scenario?: string | null;
            /** Book Id */
            book_id?: string | null;
            /** Created At */
            created_at: string;
            /** Email */
            email: string;
            /** Has Book */
            has_book: boolean;
            /** User Id */
            user_id: string;
        };
        /**
         * Money
         * @description One money figure, in the only two forms the API ever ships.
         */
        Money: {
            /** Display */
            display: string;
            /** Exact */
            exact: string;
        };
        /**
         * MoneyMove
         * @description One figure, before and after.
         */
        MoneyMove: {
            after: components["schemas"]["Money"] | null;
            before: components["schemas"]["Money"] | null;
            change: components["schemas"]["Money"] | null;
        };
        /** NegativeMonth */
        NegativeMonth: {
            depth: components["schemas"]["Money"];
            /**
             * Period
             * Format: date
             */
            period: string;
        };
        /** PeriodMove */
        PeriodMove: {
            /** After */
            after?: string | null;
            /** Before */
            before?: string | null;
            /** Period */
            period: string | null;
        };
        /** ProposalAction */
        ProposalAction: {
            /**
             * Action
             * @enum {string}
             */
            action: "accept" | "discard";
        };
        /**
         * ProposalOut
         * @description A proposal card, as the API ships it (SPEC §6-S4).
         */
        ProposalOut: {
            /** Base Revision */
            base_revision: string | null;
            /** Created At */
            created_at: string;
            deltas: components["schemas"]["Deltas"];
            /** Diagnostics */
            diagnostics: components["schemas"]["DiagnosticOut"][];
            /** Expires At */
            expires_at: string;
            /** Id */
            id: string;
            /** Operations */
            operations: {
                [key: string]: unknown;
            }[];
            /** Origin */
            origin: string;
            /** Scenario */
            scenario: string;
            /** Status */
            status: string;
            /** Supersedes */
            supersedes?: string | null;
            /** Turn Id */
            turn_id: string | null;
        };
        /**
         * ProposalResponse
         * @description ``kind`` mirrors ``POST /turns`` so a client renders one shape.
         *
         *     ``proposal`` for a change awaiting confirmation, ``clarification`` when the
         *     service needs an answer before it can build one — never a guess.
         */
        ProposalResponse: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /** Clarification */
            clarification?: string | null;
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            /** Kind */
            kind: string;
            proposal?: components["schemas"]["ProposalOut"] | null;
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Scenario */
            scenario: string;
            what_if: components["schemas"]["WhatIf"];
        };
        /**
         * Receipt
         * @description One executed read operation, and what the engine answered.
         */
        Receipt: {
            /** Op */
            op: string;
            /** Payload */
            payload: {
                [key: string]: unknown;
            };
            /** Request */
            request: {
                [key: string]: unknown;
            };
            /** Scenario */
            scenario: string;
        };
        /** ReconcileResponse */
        ReconcileResponse: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            reconciliation: components["schemas"]["ReconciliationOut"];
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Scenario */
            scenario: string;
            what_if: components["schemas"]["WhatIf"];
        };
        /** ReconciliationLineOut */
        ReconciliationLineOut: {
            actual: components["schemas"]["Money"];
            drift: components["schemas"]["Money"];
            forecast: components["schemas"]["Money"];
            /** Item Id */
            item_id: string;
        };
        /** ReconciliationOut */
        ReconciliationOut: {
            /** Actual Events */
            actual_events: number;
            actual_total: components["schemas"]["Money"];
            /** Diagnostics */
            diagnostics: components["schemas"]["DiagnosticOut"][];
            drift_total: components["schemas"]["Money"];
            forecast_total: components["schemas"]["Money"];
            /** Lines */
            lines: components["schemas"]["ReconciliationLineOut"][];
            /** Measure */
            measure: string;
            /** Reconciled */
            reconciled: boolean;
            /**
             * Since
             * Format: date
             */
            since: string;
            /**
             * Suggested Cutover
             * Format: date
             */
            suggested_cutover: string;
            /**
             * Until
             * Format: date
             */
            until: string;
        };
        /**
         * ReconciliationReport
         * @description The whole report, per sheet row plus the counts (SPEC §6-S14).
         */
        ReconciliationReport: {
            /**
             * Call Cap
             * @default 0
             */
            call_cap: number;
            /**
             * Capped
             * @default false
             */
            capped: boolean;
            /** Checks */
            checks?: components["schemas"]["CheckResult"][];
            /** Created Fork */
            created_fork: boolean;
            /**
             * Incomplete Reason
             * @default
             */
            incomplete_reason: string;
            /**
             * Llm Calls
             * @default 0
             */
            llm_calls: number;
            /**
             * Matched
             * @default 0
             */
            matched: number;
            /**
             * Mismatched
             * @default 0
             */
            mismatched: number;
            /**
             * Parity Notes
             * @default 0
             */
            parity_notes: number;
            /**
             * Parity Tolerance
             * @default 0.01
             */
            parity_tolerance: string;
            /**
             * Partial
             * @default false
             */
            partial: boolean;
            /**
             * Skipped
             * @default 0
             */
            skipped: number;
            /** Source Filename */
            source_filename: string;
            /**
             * Target Reason
             * @enum {string}
             */
            target_reason: "empty_book" | "non_empty_book";
            /** Target Scenario */
            target_scenario: string;
        };
        /**
         * RecordActual
         * @description Host op — the M5 record-actual channel (SPEC §5-F5).
         *
         *     This op *is* the ``context: "actuals_record"`` flow in typed form. Whether
         *     it becomes an actual or a forecast is still the discriminator's decision,
         *     not the caller's: a future-dated entry on this flow stays ``forecast``, and
         *     a missing date is a clarification, never a guess.
         */
        RecordActual: {
            /**
             * Amount
             * @example -912.50
             */
            amount: string;
            /** Date */
            date?: string | null;
            /** Direction */
            direction?: ("in" | "out") | null;
            /** Id */
            id?: string | null;
            /** Item */
            item?: string | null;
            /** Note */
            note?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "record_actual";
            /** Scenario */
            scenario?: string | null;
        };
        /**
         * RemoveEvent
         * @description Host op — remove one event. Refused on an actual (SPEC §2.5).
         *
         *     An actual is a fact. Removing the record of a fact destroys it; correcting
         *     it is M6, which leaves a scar (ADR-0012). The applier refuses rather than
         *     choosing for the user.
         */
        RemoveEvent: {
            /** Event */
            event: string;
            /**
             * Note
             * @default removed from the plan
             */
            note: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "remove_event";
            /** Scenario */
            scenario?: string | null;
        };
        /** RevisionOut */
        RevisionOut: {
            /** Author */
            author: string;
            /** Depth */
            depth: number;
            /** Engine Version */
            engine_version?: string | null;
            /** Id */
            id: string;
            /** Message */
            message: string;
            /** Parent */
            parent: string | null;
            /** Timestamp */
            timestamp: string;
        };
        /**
         * Save
         * @description M9 — ``save``, which is ``commit()``.
         *
         *     It is an intent so a turn can express it, but it does not run through the
         *     dry-run applier: committing is not a change to the working overlay, it is
         *     the act of recording one. ``POST /book/save`` is its endpoint.
         */
        Save: {
            /** Message */
            message: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "save";
            /** Scenario */
            scenario?: string | null;
        };
        /** SaveRequest */
        SaveRequest: {
            /** Message */
            message: string;
        };
        /** SaveResponse */
        SaveResponse: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /** Committed */
            committed: boolean;
            /**
             * Diagnostics
             * @default []
             */
            diagnostics: unknown[];
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Scenario */
            scenario: string;
            /**
             * Superseded
             * @default []
             */
            superseded: string[];
            what_if: components["schemas"]["WhatIf"];
        };
        /**
         * ScaleItems
         * @description M4 — ``scale_items``, the ScaleItems macro.
         */
        ScaleItems: {
            /**
             * Factor
             * @example 0.8
             */
            factor: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "scale_items";
            /** Scenario */
            scenario?: string | null;
            /** Selector */
            selector: string;
        };
        /** ScenarioOut */
        ScenarioOut: {
            /** Id */
            id: string;
            /** Is Active */
            is_active: boolean;
            /** Is Base */
            is_base: boolean;
            /** Note */
            note: string;
            /** Parent */
            parent: string | null;
        };
        /** ScenariosResponse */
        ScenariosResponse: {
            /** Active */
            active: string;
            /** Scenarios */
            scenarios: components["schemas"]["ScenarioOut"][];
        };
        /** Session */
        Session: {
            /** Expires At */
            expires_at: string;
            /**
             * Platform
             * @enum {string}
             */
            platform: "web" | "mobile";
            /** Token */
            token: string;
        };
        /**
         * SetAmount
         * @description M2 — ``set_amount``; ``from_date`` splits the segment.
         */
        SetAmount: {
            /**
             * Amount
             * @example -912.50
             */
            amount: string;
            /** From Date */
            from_date?: string | null;
            /** Item */
            item: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "set_amount";
            /** Scenario */
            scenario?: string | null;
        };
        /**
         * SetCutover
         * @description M8 — ``set_cutover``.
         */
        SetCutover: {
            /**
             * Date
             * Format: date
             */
            date: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "set_cutover";
            /** Scenario */
            scenario?: string | null;
        };
        /**
         * SetHorizon
         * @description Host op — move the book's horizon.
         */
        SetHorizon: {
            /**
             * End
             * Format: date
             */
            end: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "set_horizon";
            /** Scenario */
            scenario?: string | null;
            /**
             * Start
             * Format: date
             */
            start: string;
        };
        /**
         * SetOpeningBalance
         * @description Host op — restate the opening balance.
         */
        SetOpeningBalance: {
            /**
             * Amount
             * @example -912.50
             */
            amount: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "set_opening_balance";
            /** Scenario */
            scenario?: string | null;
        };
        /**
         * ShiftItems
         * @description M3 — ``shift_items``, the ShiftItems macro.
         */
        ShiftItems: {
            /**
             * By
             * @example 2m
             * @example 30d
             */
            by: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            op: "shift_items";
            /** Scenario */
            scenario?: string | null;
            /** Selector */
            selector: string;
        };
        /** StepOut */
        StepOut: {
            /** Expression */
            expression: string;
            /** Inputs */
            inputs: string[];
            /** Operation */
            operation: string;
            /** Rounding */
            rounding: string;
            value: components["schemas"]["Money"];
        };
        /**
         * SummaryOut
         * @description ``RunSummary``, field for field.
         */
        SummaryOut: {
            /** Balance Source */
            balance_source: string;
            /** Breakeven Period */
            breakeven_period: string | null;
            closing_balance: components["schemas"]["Money"];
            /** Diagnostics */
            diagnostics: components["schemas"]["DiagnosticOut"][];
            /** Grain */
            grain: string;
            min_cash: components["schemas"]["Money"];
            /** Min Cash Period */
            min_cash_period: string | null;
            net_cash: components["schemas"]["Money"];
            opening_balance: components["schemas"]["Money"];
            /** Periods */
            periods: number;
            /** Runway End */
            runway_end: string | null;
            /** Runway Periods */
            runway_periods: number | null;
            total_accrual: components["schemas"]["Money"];
            total_inflow: components["schemas"]["Money"];
            total_outflow: components["schemas"]["Money"];
        };
        /** TraceOut */
        TraceOut: {
            /** Bindings */
            bindings: components["schemas"]["BindingOut"][];
            /** Children */
            children: components["schemas"]["TraceOut"][];
            /** Depth */
            depth: number;
            /** Diagnostics */
            diagnostics: components["schemas"]["DiagnosticOut"][];
            /** Formula */
            formula: string;
            /** Item Id */
            item_id: string;
            /** Item Name */
            item_name: string;
            /** Kind */
            kind: string;
            /** Measure */
            measure: string;
            /** Notes */
            notes: string[];
            /**
             * Period End
             * Format: date
             */
            period_end: string;
            /** Period Index */
            period_index: number;
            /**
             * Period Start
             * Format: date
             */
            period_start: string;
            /** Reconciles */
            reconciles: boolean;
            /** Steps */
            steps: components["schemas"]["StepOut"][];
            /** Truncated */
            truncated: boolean;
            value: components["schemas"]["Money"];
        };
        /** TraceResponse */
        TraceResponse: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            /** Measure */
            measure: string;
            /**
             * Period
             * Format: date
             */
            period: string;
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Scenario */
            scenario: string;
            trace: components["schemas"]["TraceOut"];
            what_if: components["schemas"]["WhatIf"];
        };
        /**
         * TurnRequest
         * @description ``POST /turns {text, scenario?, context?}``.
         *
         *     ``context: "actuals_record"`` marks the record-actual channel (SPEC §5-F5).
         *     It is set by the interface on the Actuals record flow and passed straight
         *     through to the discriminator, which the model never influences.
         */
        TurnRequest: {
            /** Context */
            context?: "actuals_record" | null;
            /** Scenario */
            scenario?: string | null;
            /** Text */
            text: string;
        };
        /**
         * TurnResponse
         * @description ``{kind, reply, receipts[], proposal?}`` plus the SPEC §11 chain.
         *
         *     ``kind`` has four values, not three: SPEC §3 lists ``answer``,
         *     ``proposal`` and ``clarification``, and SPEC §8 requires a turn over the
         *     daily budget to "refuse politely with a retry-tomorrow message". That
         *     refusal is a turn outcome the user reads as a sentence, not an
         *     infrastructure error, so it is a fourth kind rather than a status code
         *     (D-MLP-24).
         */
        TurnResponse: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /** Clarification */
            clarification?: string | null;
            /**
             * Diagnostics
             * @default []
             */
            diagnostics: components["schemas"]["DiagnosticOut"][];
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "answer" | "proposal" | "clarification" | "refusal";
            /**
             * Llm Calls
             * @default 0
             */
            llm_calls: number;
            proposal?: components["schemas"]["ProposalOut"] | null;
            /**
             * Receipts
             * @default []
             */
            receipts: components["schemas"]["Receipt"][];
            /** Reply */
            reply: string;
            /** Request Id */
            request_id: string;
            /** Retry After Seconds */
            retry_after_seconds?: number | null;
            /** Revision */
            revision: string | null;
            /** Scenario */
            scenario: string;
            /** Turn Id */
            turn_id: string;
            what_if: components["schemas"]["WhatIf"];
        };
        /** ValidateResponse */
        ValidateResponse: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /** Diagnostics */
            diagnostics: unknown[];
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Scenario */
            scenario: string;
            what_if: components["schemas"]["WhatIf"];
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** VerifyRequest */
        VerifyRequest: {
            /**
             * Platform
             * @default web
             * @enum {string}
             */
            platform: "web" | "mobile";
            /** Token */
            token: string;
        };
        /**
         * Warnings
         * @description Standing, structural warnings. No thresholds, nothing configurable.
         *
         *     Computed at every update, never on a schedule (D-MLP-05(b)): the state
         *     payload always reflects the book as it is right now.
         */
        Warnings: {
            min_cash: components["schemas"]["Money"];
            /** Min Cash Period */
            min_cash_period: string | null;
            /** Negative Months */
            negative_months: components["schemas"]["NegativeMonth"][];
        };
        /**
         * WhatIf
         * @description The §2.4 payload field.
         */
        WhatIf: {
            /** Reason */
            reason?: ("scenario" | "overlay" | "pending") | null;
            /** Scenario */
            scenario?: string | null;
            /**
             * Stamped
             * @default false
             */
            stamped: boolean;
        };
        /** WhyZeroResponse */
        WhyZeroResponse: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /**
             * Engine Version
             * @default 1
             */
            engine_version: string;
            explanation: components["schemas"]["ExplanationOut"];
            /** Measure */
            measure: string;
            /**
             * Period
             * Format: date
             */
            period: string;
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: string | null;
            /** Scenario */
            scenario: string;
            what_if: components["schemas"]["WhatIf"];
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    request_link_auth_link_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LinkRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    verify_link_auth_verify_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["VerifyRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Session"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    compare_scenarios_book_compare_get: {
        parameters: {
            query: {
                metric?: string;
                scenarios: string;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CompareResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    discard_working_overlay_book_discard_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DiscardResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_edit_proposal_book_edits_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EditsRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProposalResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_events_book_events_get: {
        parameters: {
            query?: {
                include_voided?: boolean;
                scenario?: string | null;
                since?: string | null;
                until?: string | null;
                where?: string | null;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EventsResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_forecast_book_forecast_get: {
        parameters: {
            query?: {
                grain?: string | null;
                scenario?: string | null;
                start?: string | null;
                window?: number | null;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Forecast"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_history_book_history_get: {
        parameters: {
            query?: {
                limit?: number;
                scenario?: string | null;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HistoryResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_reconcile_book_reconcile_get: {
        parameters: {
            query?: {
                scenario?: string | null;
                since?: string | null;
                until?: string | null;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReconcileResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    save_book_book_save_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SaveRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SaveResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_scenarios_book_scenarios_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScenariosResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_scenario_book_scenarios_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateScenario"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    activate_scenario_book_scenarios__scenario_id__activate_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                scenario_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ActivateResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_state_book_state_get: {
        parameters: {
            query?: {
                scenario?: string | null;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BookState"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_trace_book_trace_get: {
        parameters: {
            query: {
                depth?: number;
                item: string;
                measure?: string;
                period: string;
                scenario?: string | null;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TraceResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_validate_book_validate_get: {
        parameters: {
            query?: {
                scenario?: string | null;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ValidateResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_why_zero_book_why_zero_get: {
        parameters: {
            query: {
                item: string;
                measure?: string;
                period: string;
                scenario?: string | null;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WhyZeroResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_book_endpoint_books_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateBook"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BookCreated"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export_workbook_export_get: {
        parameters: {
            query?: {
                mode?: "ledger" | "budget";
                months?: number;
                scenario?: string | null;
                start?: string | null;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The book as a workbook. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    start_import_import_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": components["schemas"]["Body_start_import_import_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImportStarted"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    read_import_imports__job_id__get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImportDone"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    stream_import_imports__job_id__stream_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Progress as it happens: stage, section, and every reconciliation check passing or failing, ending with the `done` event whose data is an ImportDone. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/event-stream": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_me_me_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Me"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_me_me_delete: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export_me_me_export_get: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Everything the account owns. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/zip": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resolve_proposal_proposals__proposal_id__post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path: {
                proposal_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProposalAction"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AcceptResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_turn_turns_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TurnRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TurnResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}
