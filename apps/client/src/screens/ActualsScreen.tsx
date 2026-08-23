/**
 * Screen 7 — Actuals, corrections and cutover (SPEC §6-S7, §5-F5).
 *
 * Three things happen here that happen nowhere else in the app.
 *
 * **This is the record-actual flow, and the client's only job is the flag.**
 * SPEC §5-F5: an M5 intent becomes `status="actual"` if and only if the turn
 * arrived with `context: "actuals_record"` — set by the client only on this
 * screen — and the event date is ≤ `as_of`. Everything after that is the
 * service's (`ops/applier.py::discriminate_event_status`, T18). The client does
 * not check the date, does not guess a status, and does not re-implement any
 * part of the rule; it sends the flag and renders what came back. A missing or
 * ambiguous date comes back as `kind: clarification` with nothing stored, and
 * that is rendered as the question it is.
 *
 * **Corrections leave a scar.** M6 only, note mandatory, append-only. The
 * original stays visible and struck with the correction linked (ADR-0012/0013).
 * `include_voided=true` on the ledger read is what puts the tombstoned original
 * on the wire; without it there is nothing to strike.
 *
 * **R10 renders verbatim.** `validate()` diagnostics are model-consistency
 * findings, not advice and not a score (ADR-0021, D-MLP-02). They are never
 * rewritten, summarized, suppressed or reframed (ADR-0015), which is why they
 * go through the same `DiagnosticList` every other surface uses and why the
 * gate compares the rendered text against the endpoint's own JSON.
 *
 * Every figure on this screen is the engine's. The recorded total is
 * `reconciliation.actual_total` and the month-end figure is the closing series
 * selected by index — the screen adds nothing up.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ScrollView, Text, View, StyleSheet } from "react-native";

import type {
  Diagnostic,
  EventsResponse,
  LedgerEvent,
  ReconcileResponse,
} from "@cashkit/api-types";

import { api, describeError } from "../api/client";
import { asDiagnostics } from "../api/diagnostics";
import { formatMoney, moneyTone } from "../money/money";
import { useBook } from "../state/book";
import { useConversation } from "../state/conversation";
import { useEditProposal } from "../state/edits";
import { Button, Card, Divider, LeaderRow, Stamp } from "../ui/atoms";
import { AsOfLine, DiagnosticList, monthLabel, shortDate, WhatIfStamp } from "../ui/provenance";
import { EmptyState, ErrorState, LoadingState } from "../ui/states";
import { color, font, space } from "../ui/tokens";
import { AnswerCard } from "./components/AnswerCard";
import { AskBar } from "./components/AskBar";
import { CorrectionForm } from "./components/CorrectionForm";
import { LedgerRowList, linkCorrections } from "./components/LedgerRows";
import { ProposalCard } from "./components/ProposalCard";

const ZERO = new Set(["0.0000", "-0.0000"]);

/** The first day of the month containing an ISO date. String work only. */
function monthStart(iso: string): string {
  return `${iso.slice(0, 7)}-01`;
}

export function ActualsScreen({
  onBack,
  onOpenPlan,
  testID = "actuals-screen",
}: {
  onBack: () => void;
  onOpenPlan?: () => void;
  testID?: string;
}) {
  const book = useBook();
  const conversation = useConversation();

  const [events, setEvents] = useState<EventsResponse | null>(null);
  const [reconcile, setReconcile] = useState<ReconcileResponse | null>(null);
  const [diagnostics, setDiagnostics] = useState<Diagnostic[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [correcting, setCorrecting] = useState<LedgerEvent | null>(null);

  const state = book.state;
  const asOf = state?.as_of ?? null;

  const load = useCallback(async () => {
    if (!asOf) return;
    setLoading(true);
    const since = monthStart(asOf);
    const [ledger, report, validate] = await Promise.all([
      // `include_voided` is what makes the scar visible (ADR-0012).
      api.GET("/book/events", {
        params: { query: { since, until: asOf, include_voided: true } },
      }),
      api.GET("/book/reconcile", { params: { query: { since, until: asOf } } }),
      api.GET("/book/validate", { params: { query: {} } }),
    ]);
    setLoading(false);
    if (ledger.error || !ledger.data) {
      setError(describeError(ledger.error, ledger.response.status));
      return;
    }
    setError(null);
    setEvents(ledger.data);
    setReconcile(report.data ?? null);
    // R10, verbatim. Nothing here filters by severity or by interest.
    setDiagnostics(asDiagnostics(validate.data?.diagnostics));
  }, [asOf]);

  useEffect(() => {
    void load();
  }, [load]);

  const refreshAll = useCallback(async () => {
    await book.refresh();
    await load();
  }, [book, load]);

  const edit = useEditProposal({ onApplied: refreshAll });

  const recordCorrection = useCallback(
    async (amount: string, note: string) => {
      if (!correcting) return;
      // M6, the one operation that may touch an actual. The note travels with
      // it because it is part of the record, not a UI courtesy.
      await edit.propose([{ op: "correct_actual", event: correcting.id, amount, note }], {
        origin: "cell_edit",
      });
      setCorrecting(null);
    },
    [correcting, edit],
  );

  const moveCutover = useCallback(async () => {
    const suggested = reconcile?.reconciliation.suggested_cutover;
    if (!suggested) return;
    await edit.propose([{ op: "set_cutover", date: suggested }], { origin: "settings" });
  }, [reconcile, edit]);

  const rows = useMemo(() => {
    if (!events) return [];
    // The ledger holds forecast events too; the recorded card is the actuals.
    // A tombstoned original of an actual stays, which is the scar.
    const actuals = events.events.filter((e) => e.status === "actual");
    return linkCorrections(actuals);
  }, [events]);

  /**
   * The card awaiting the user, from either source.
   *
   * Two paths raise a proposal on this screen and they are the same pipeline
   * with different origins: a recorded actual comes from a **turn**, a
   * correction or a cutover from `POST /book/edits`. Only one card is ever
   * live — the service supersedes the rest — so the screen shows one region
   * and remembers which path to post the confirmation back to.
   */
  const turnProposal = useMemo(() => {
    if (!conversation.pendingProposalId) return null;
    for (let i = conversation.entries.length - 1; i >= 0; i -= 1) {
      const entry = conversation.entries[i];
      if (entry?.kind === "turn" && entry.response.proposal?.id === conversation.pendingProposalId) {
        return entry.response.proposal;
      }
      if (entry?.kind === "resolution" && entry.response.proposal.id === conversation.pendingProposalId) {
        return entry.response.proposal;
      }
    }
    return null;
  }, [conversation.entries, conversation.pendingProposalId]);

  const pending = turnProposal
    ? ({ source: "turn", proposal: turnProposal } as const)
    : edit.pending
      ? ({ source: "edit", proposal: edit.pending } as const)
      : null;

  const resolveCard = useCallback(
    async (action: "accept" | "discard") => {
      if (!pending) return;
      if (pending.source === "turn") {
        await conversation.resolve(pending.proposal.id, action);
        await refreshAll();
      } else {
        await edit.resolve(action);
      }
    },
    [pending, conversation, edit, refreshAll],
  );

  /** The last turn-path confirmation outcome, for the applied/discarded stamp. */
  const turnResolution = useMemo(() => {
    for (let i = conversation.entries.length - 1; i >= 0; i -= 1) {
      const entry = conversation.entries[i];
      if (entry?.kind === "resolution") return entry.response;
      if (entry?.kind === "turn") return null;
    }
    return null;
  }, [conversation.entries]);

  const resolution = turnResolution ?? edit.resolution;

  /** An answer, a clarification or a refusal — never a proposal, which has a card. */
  const lastTurn = useMemo(() => {
    for (let i = conversation.entries.length - 1; i >= 0; i -= 1) {
      const entry = conversation.entries[i];
      if (entry && entry.kind === "turn") {
        return entry.response.kind === "proposal" ? null : entry.response;
      }
    }
    return null;
  }, [conversation.entries]);

  if (book.loading && !state) return <LoadingState label="Opening your book…" />;
  if (book.error) return <ErrorState message={book.error} onRetry={() => void book.refresh()} />;
  if (!state) {
    return (
      <View style={styles.screen}>
        <EmptyState
          title="No book to record against yet."
          example="I earn 3,000 a month and pay 900 rent"
          testID={`${testID}-no-book`}
        />
      </View>
    );
  }
  if (loading && !events) return <LoadingState label="Reading the ledger…" />;
  if (error) return <ErrorState message={error} onRetry={() => void load()} testID={`${testID}-error`} />;

  const monthIndex = state.months.findIndex((m) => m.slice(0, 7) === state.as_of.slice(0, 7));
  const monthEnd = monthIndex >= 0 ? (state.closing[monthIndex] ?? null) : null;
  const stillComputed = state.items
    .map((item) => ({ item, value: monthIndex >= 0 ? (item.cash[monthIndex] ?? null) : null }))
    .filter((row) => row.value !== null && !ZERO.has(row.value.exact));
  const report = reconcile?.reconciliation ?? null;
  const cutoverIsCurrent =
    report !== null && report.suggested_cutover.slice(0, 10) === state.book.cutover.slice(0, 10);

  return (
    <View testID={testID} style={styles.screen}>
      <View style={styles.headerRow}>
        <Text testID={`${testID}-back`} style={styles.back} onPress={onBack}>
          ‹ BACK
        </Text>
        <Text style={styles.title}>Actuals</Text>
        <View style={styles.chip}>
          <Text testID={`${testID}-month`} style={styles.chipLabel}>
            {monthLabel(state.as_of)}
          </Text>
        </View>
      </View>

      <Text testID={`${testID}-subline`} style={styles.subline}>
        {`CUTOVER ${shortDate(state.book.cutover)} · LEDGER AUTHORITATIVE`}
      </Text>
      <WhatIfStamp whatIf={state.what_if} testID={`${testID}-what-if`} />

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        <Card testID={`${testID}-recorded-card`}>
          <Stamp tone="sub" testID={`${testID}-recorded-label`}>
            {`RECORDED · ${shortDate(monthStart(state.as_of))} – ${shortDate(state.as_of)}`}
          </Stamp>

          {rows.length === 0 ? (
            <EmptyState
              title="Nothing recorded this month yet."
              example="groceries on the 9th were 69"
              testID={`${testID}-recorded-empty`}
            />
          ) : (
            <LedgerRowList
              rows={rows}
              testID={`${testID}-ledger`}
              onCorrect={(event) => {
                edit.reset();
                setCorrecting(event);
              }}
            />
          )}

          {report ? (
            <>
              <Divider strong />
              <LeaderRow
                testID={`${testID}-recorded-total`}
                label={`Recorded to ${shortDate(report.until)}`}
                meta={`${report.actual_events} ledger rows · measure ${report.measure}`}
                value={formatMoney(report.actual_total)}
                tone={moneyTone(report.actual_total)}
                emphasis
              />
            </>
          ) : null}
        </Card>

        {correcting ? (
          <CorrectionForm
            event={correcting}
            busy={edit.busy}
            testID={`${testID}-correction-form`}
            onCancel={() => setCorrecting(null)}
            onSubmit={(amount, note) => void recordCorrection(amount, note)}
          />
        ) : null}

        {edit.error ? <ErrorState message={edit.error} testID={`${testID}-edit-error`} /> : null}
        {edit.clarification ? (
          <Card testID={`${testID}-edit-clarification`}>
            <Text style={styles.clarification}>{edit.clarification}</Text>
            <Stamp>NOTHING WAS RECORDED</Stamp>
          </Card>
        ) : null}
        {pending ? (
          <ProposalCard
            proposal={pending.proposal}
            busy={edit.busy || conversation.busy}
            testID={`${testID}-proposal-card`}
            onApply={() => void resolveCard("accept")}
            onDiscard={() => void resolveCard("discard")}
            onEdit={() => undefined}
          />
        ) : null}
        {!pending && resolution && resolution.kind !== "refreshed" ? (
          <Stamp
            testID={`${testID}-resolution`}
            tone={resolution.kind === "applied" ? "pine" : "faint"}
          >
            {resolution.kind === "applied"
              ? `APPLIED · REV ${resolution.revision ? resolution.revision.slice(0, 7) : "UNCOMMITTED"}`
              : "DISCARDED"}
          </Stamp>
        ) : null}

        <Card testID={`${testID}-computed-card`}>
          <Stamp tone="sub" testID={`${testID}-computed-label`}>
            {`STILL COMPUTED · ${monthLabel(state.as_of).toUpperCase()}`}
          </Stamp>
          {stillComputed.length === 0 ? (
            <Stamp testID={`${testID}-computed-empty`}>NOTHING GENERATED FOR THIS MONTH</Stamp>
          ) : (
            stillComputed.map(({ item, value }) => (
              <LeaderRow
                key={item.id}
                testID={`${testID}-computed-${item.id}`}
                label={item.name}
                meta={`item:${item.id} · ${item.kind}${item.direction ? ` · ${item.direction}` : ""}`}
                value={formatMoney(value)}
                tone={moneyTone(value)}
              />
            ))
          )}
          <Divider strong />
          <LeaderRow
            testID={`${testID}-month-end`}
            label={`${monthLabel(state.as_of)} ends`}
            value={formatMoney(monthEnd)}
            tone={moneyTone(monthEnd)}
            emphasis
          />
        </Card>

        {/* Cutover (M8): offered after a reconcile, applied as a proposal. */}
        {report && !cutoverIsCurrent ? (
          <Card testID={`${testID}-cutover-card`}>
            <Stamp tone="sub">CUTOVER</Stamp>
            <Text style={styles.cutoverText}>
              {`The ledger is authoritative up to ${shortDate(report.suggested_cutover)}. ` +
                `The book's cutover is still ${shortDate(state.book.cutover)}.`}
            </Text>
            <Button
              label={`Move the cutover to ${shortDate(report.suggested_cutover)}`}
              testID={`${testID}-cutover-apply`}
              disabled={edit.busy}
              onPress={() => void moveCutover()}
            />
          </Card>
        ) : null}

        {/* R10 — validate() model-consistency diagnostics, verbatim. */}
        <Card testID={`${testID}-diagnostics-card`}>
          <Stamp tone="sub" testID={`${testID}-diagnostics-label`}>
            CHECKS · FROM THE ENGINE, WORD FOR WORD
          </Stamp>
          {diagnostics && diagnostics.length > 0 ? (
            <DiagnosticList diagnostics={diagnostics} testID={`${testID}-diagnostics`} />
          ) : (
            <Stamp testID={`${testID}-diagnostics-none`}>
              THE ENGINE REPORTS NOTHING ABOUT THIS MODEL
            </Stamp>
          )}
        </Card>

        {report ? (
          <DiagnosticList
            diagnostics={report.diagnostics}
            testID={`${testID}-reconcile-diagnostics`}
          />
        ) : null}

        {lastTurn ? <AnswerCard turn={lastTurn} testID={`${testID}-answer-card`} /> : null}
      </ScrollView>

      <View style={styles.footer}>
        <View style={styles.footerRow}>
          <AsOfLine asOf={state.as_of} scenario={state.scenario} testID={`${testID}-as-of`} />
          {onOpenPlan ? (
            <Text testID={`${testID}-plan-link`} style={styles.link} onPress={onOpenPlan}>
              PLAN VS ACTUAL ›
            </Text>
          ) : null}
        </View>
        {/* The one place in the app that sets the record-actual discriminator. */}
        <AskBar
          placeholder="“groceries on the 9th were 69…”"
          disabled={conversation.busy}
          testID={`${testID}-ask`}
          onSubmit={(text) => {
            void (async () => {
              await conversation.ask(text, { context: "actuals_record" });
              await refreshAll();
            })();
          }}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: color.paper,
    paddingHorizontal: space.screenX,
    paddingTop: space.screenTop,
    paddingBottom: space.screenBottom,
    gap: 8,
  },
  headerRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  back: { fontFamily: font.mono, fontSize: 10, letterSpacing: 0.7, color: color.sub },
  title: { fontFamily: font.display, fontSize: 28, fontWeight: "600", color: color.ink, flex: 1 },
  chip: { backgroundColor: color.pineTint, borderRadius: 999, paddingVertical: 8, paddingHorizontal: 14 },
  chipLabel: { fontFamily: font.ui, fontSize: 13, fontWeight: "600", color: color.pine },
  subline: { fontFamily: font.mono, fontSize: 9.5, letterSpacing: 0.6, color: color.sub },
  body: { flex: 1 },
  bodyContent: { gap: 14, paddingBottom: 12 },
  clarification: { fontFamily: font.display, fontSize: 17, color: color.ink },
  cutoverText: { fontFamily: font.ui, fontSize: 13, color: color.sub },
  footer: { gap: 8 },
  footerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  link: { fontFamily: font.mono, fontSize: 9.5, letterSpacing: 0.6, color: color.pine },
});
