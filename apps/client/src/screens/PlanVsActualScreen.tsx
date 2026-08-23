/**
 * Screen 8 — Plan vs actual (SPEC §6-S8, §5-F5).
 *
 * `GET /book/reconcile` gives one line per item — forecast, actual, drift —
 * all three computed by the engine over the same resolved book, so the drift
 * is drift and not an artefact of how each side was worked out. The screen
 * renders those three figures and draws a bar for the ratio between two of
 * them. It adds nothing up.
 *
 * **The category view has no subtotals, and that is a decision.** SPEC §6-S8's
 * design shows a subtotal beside each category header. `GET /book/reconcile`
 * has no grouping and no group totals, and a subtotal is a sum of money — the
 * one thing the client may never compute (PROMPT non-negotiable 1). Grouping
 * the rows under their tag is not arithmetic and is done; adding them up is,
 * and is not. The header says so rather than showing a figure nobody computed
 * (D-MLP-62).
 *
 * **Unsettled is not zero.** `reconciliation.actual` is `0.0000` both for a row
 * with nothing recorded against it and for a row that genuinely came to
 * nothing, and the payload cannot tell them apart. The ledger can: a row with
 * no `status="actual"` event in the window has not settled, and it draws an
 * empty track rather than a bar at zero (SPEC §6-S8).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ScrollView, Text, View, StyleSheet } from "react-native";

import type { EventsResponse, ItemSeries, ReconcileResponse } from "@cashkit/api-types";

import { api, describeError } from "../api/client";
import { formatMoney, moneyTone } from "../money/money";
import { useBook } from "../state/book";
import { useConversation } from "../state/conversation";
import { Card, Divider, LeaderRow, Stamp } from "../ui/atoms";
import { AsOfLine, DiagnosticList, monthLabel, shortDate, WhatIfStamp } from "../ui/provenance";
import { EmptyState, ErrorState, LoadingState } from "../ui/states";
import { color, font, space } from "../ui/tokens";
import { AnswerCard } from "./components/AnswerCard";
import { AskBar } from "./components/AskBar";
import { PlanBar } from "./components/PlanBar";

type View_ = "category" | "item";

const ZERO = new Set(["0.0000", "-0.0000"]);

function monthStart(iso: string): string {
  return `${iso.slice(0, 7)}-01`;
}

/** The category a row belongs to: its item's `cat` tag, or "uncategorized". */
function categoryOf(item: ItemSeries | undefined): string {
  if (!item) return "unmatched";
  return item.tags["cat"] ?? item.tags["category"] ?? "uncategorized";
}

export function PlanVsActualScreen({
  onBack,
  onOpenTrace,
  testID = "plan-screen",
}: {
  onBack: () => void;
  onOpenTrace: (period: string, scenario: string) => void;
  testID?: string;
}) {
  const book = useBook();
  const conversation = useConversation();

  const [reconcile, setReconcile] = useState<ReconcileResponse | null>(null);
  const [events, setEvents] = useState<EventsResponse | null>(null);
  const [grouping, setGrouping] = useState<View_>("item");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const state = book.state;
  const asOf = state?.as_of ?? null;

  const load = useCallback(async () => {
    if (!asOf) return;
    setLoading(true);
    const since = monthStart(asOf);
    const [report, ledger] = await Promise.all([
      api.GET("/book/reconcile", { params: { query: { since, until: asOf } } }),
      api.GET("/book/events", { params: { query: { since, until: asOf } } }),
    ]);
    setLoading(false);
    if (report.error || !report.data) {
      setError(describeError(report.error, report.response.status));
      return;
    }
    setError(null);
    setReconcile(report.data);
    setEvents(ledger.data ?? null);
  }, [asOf]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Which items the ledger has actually recorded something for, this window. */
  const settledItems = useMemo(() => {
    const seen = new Set<string>();
    for (const event of events?.events ?? []) {
      if (event.status !== "actual") continue;
      seen.add(event.item ?? "");
    }
    return seen;
  }, [events]);

  const itemsById = useMemo(() => {
    const map = new Map<string, ItemSeries>();
    for (const item of state?.items ?? []) map.set(item.id, item);
    return map;
  }, [state]);

  const lastTurn = useMemo(() => {
    for (let i = conversation.entries.length - 1; i >= 0; i -= 1) {
      const entry = conversation.entries[i];
      if (entry && entry.kind === "turn") return entry.response;
    }
    return null;
  }, [conversation.entries]);

  if (book.loading && !state) return <LoadingState label="Opening your book…" />;
  if (!state) {
    return (
      <View style={styles.screen}>
        <EmptyState
          title="Nothing to compare yet."
          example="I earn 3,000 a month and pay 900 rent"
          testID={`${testID}-no-book`}
        />
      </View>
    );
  }
  if (loading && !reconcile) return <LoadingState label="Reconciling…" />;
  if (error) return <ErrorState message={error} onRetry={() => void load()} testID={`${testID}-error`} />;
  if (!reconcile) return <EmptyState title="No reconciliation." example="what did I spend this month?" />;

  const report = reconcile.reconciliation;
  const lines = report.lines;

  const groups: { key: string; lines: typeof lines }[] =
    grouping === "item"
      ? [{ key: "all items", lines }]
      : Array.from(
          lines.reduce((acc, line) => {
            const key = categoryOf(itemsById.get(line.item_id));
            const bucket = acc.get(key);
            if (bucket) bucket.push(line);
            else acc.set(key, [line]);
            return acc;
          }, new Map<string, typeof lines>()),
        ).map(([key, group]) => ({ key, lines: group }));

  return (
    <View testID={testID} style={styles.screen}>
      <View style={styles.headerRow}>
        <Text testID={`${testID}-back`} style={styles.back} onPress={onBack}>
          ‹ BACK
        </Text>
        <Text style={styles.title}>Plan vs actual</Text>
        <View style={styles.chip}>
          <Text testID={`${testID}-month`} style={styles.chipLabel}>
            {monthLabel(state.as_of)}
          </Text>
        </View>
      </View>

      <Text testID={`${testID}-subline`} style={styles.subline}>
        {`FORECAST ${state.revision ? state.revision.slice(0, 7) : "UNCOMMITTED"} · ACTUALS ${shortDate(
          report.since,
        )} – ${shortDate(report.until)} · Δ = ACT − FC`}
      </Text>
      <WhatIfStamp whatIf={reconcile.what_if} testID={`${testID}-what-if`} />

      <View testID={`${testID}-toggle`} style={styles.toggle}>
        {(["category", "item"] as const).map((mode) => (
          <Text
            key={mode}
            testID={`${testID}-toggle-${mode}`}
            accessibilityRole="button"
            accessibilityState={{ selected: grouping === mode }}
            onPress={() => setGrouping(mode)}
            style={[styles.toggleItem, grouping === mode && styles.toggleItemOn]}
          >
            {mode === "category" ? "By category" : "By item"}
          </Text>
        ))}
      </View>

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        <Card testID={`${testID}-summary-card`}>
          <LeaderRow
            testID={`${testID}-plan-total`}
            label="Plan"
            meta={`forecast · ${shortDate(report.since)} – ${shortDate(report.until)}`}
            value={formatMoney(report.forecast_total)}
            tone={moneyTone(report.forecast_total)}
            emphasis
          />
          <LeaderRow
            testID={`${testID}-actual-total`}
            label="Actual"
            meta={`${report.actual_events} ledger rows`}
            value={formatMoney(report.actual_total)}
            tone={moneyTone(report.actual_total)}
            emphasis
          />
          <Divider strong />
          <LeaderRow
            testID={`${testID}-drift-total`}
            label={`${monthLabel(state.as_of)} to date`}
            meta="Δ = actual − forecast, computed by the engine"
            value={formatMoney(report.drift_total)}
            tone={moneyTone(report.drift_total)}
            emphasis
          />
        </Card>

        {lines.length === 0 ? (
          <EmptyState
            title="No item has both a plan and a window yet."
            example="what did I spend on groceries this month?"
            testID={`${testID}-empty`}
          />
        ) : null}

        {groups.map((group) => (
          <Card key={group.key} testID={`${testID}-group-${group.key}`}>
            <View style={styles.groupHead}>
              <Stamp tone="sub" testID={`${testID}-group-${group.key}-label`}>
                {group.key.toUpperCase()}
              </Stamp>
              {grouping === "category" ? (
                // No subtotal: the engine did not compute one and the client
                // may not (D-MLP-62). Say why rather than show a figure.
                <Stamp testID={`${testID}-group-${group.key}-no-subtotal`}>
                  NO SUBTOTAL · THE ENGINE COMPUTES PER ITEM
                </Stamp>
              ) : null}
            </View>

            {group.lines.map((line) => {
              const item = itemsById.get(line.item_id);
              const settled = settledItems.has(line.item_id);
              const planned = !ZERO.has(line.forecast.exact);
              return (
                <View key={line.item_id} style={styles.row}>
                  <LeaderRow
                    testID={`${testID}-row-${line.item_id}`}
                    label={item?.name ?? line.item_id}
                    meta={`item:${line.item_id}${item?.direction ? ` · ${item.direction}` : ""}`}
                    value={settled ? formatMoney(line.actual) : "—"}
                    tone={settled ? moneyTone(line.actual) : "sub"}
                    emphasis
                    onPress={() => onOpenTrace(monthStart(state.as_of), state.scenario)}
                  />
                  <PlanBar
                    actual={line.actual}
                    plan={line.forecast}
                    settled={settled && planned}
                    testID={`${testID}-bar-${line.item_id}`}
                  />
                  <View style={styles.metaRow}>
                    <Stamp testID={`${testID}-plan-${line.item_id}`}>
                      {`PLAN ${formatMoney(line.forecast)}`}
                    </Stamp>
                    <Stamp
                      tone={settled ? (ZERO.has(line.drift.exact) ? "pine" : "rust") : "faint"}
                      testID={`${testID}-delta-${line.item_id}`}
                    >
                      {!settled
                        ? "NOT SETTLED"
                        : ZERO.has(line.drift.exact)
                          ? "ON PLAN"
                          : `Δ ${formatMoney(line.drift)}`}
                    </Stamp>
                  </View>
                </View>
              );
            })}
          </Card>
        ))}

        <DiagnosticList diagnostics={report.diagnostics} testID={`${testID}-diagnostics`} />

        {lastTurn ? <AnswerCard turn={lastTurn} testID={`${testID}-answer-card`} /> : null}
      </ScrollView>

      <View style={styles.footer}>
        <View style={styles.footerRow}>
          <Stamp testID={`${testID}-legend`}>
            BAR = % OF PLAN · TICK = PLAN · TAP A ROW TO TRACE
          </Stamp>
        </View>
        <AsOfLine asOf={state.as_of} scenario={state.scenario} testID={`${testID}-as-of`} />
        <AskBar
          placeholder="“why is groceries over plan?”"
          disabled={conversation.busy}
          testID={`${testID}-ask`}
          onSubmit={(text) => void conversation.ask(text)}
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
  title: { fontFamily: font.display, fontSize: 26, fontWeight: "600", color: color.ink, flex: 1 },
  chip: { backgroundColor: color.pineTint, borderRadius: 999, paddingVertical: 8, paddingHorizontal: 14 },
  chipLabel: { fontFamily: font.ui, fontSize: 13, fontWeight: "600", color: color.pine },
  subline: { fontFamily: font.mono, fontSize: 9.5, letterSpacing: 0.6, color: color.sub },
  toggle: { flexDirection: "row", gap: 8 },
  toggleItem: {
    fontFamily: font.ui,
    fontSize: 13,
    color: color.sub,
    borderWidth: 1,
    borderColor: color.hair,
    borderRadius: 999,
    paddingVertical: 6,
    paddingHorizontal: 14,
    backgroundColor: color.card,
  },
  toggleItemOn: { color: color.pine, borderColor: color.pine, backgroundColor: color.pineTint },
  body: { flex: 1 },
  bodyContent: { gap: 14, paddingBottom: 12 },
  groupHead: { flexDirection: "row", justifyContent: "space-between", width: "100%" },
  row: { width: "100%", gap: 6 },
  metaRow: { flexDirection: "row", justifyContent: "space-between", width: "100%" },
  footer: { gap: 8 },
  footerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
});
