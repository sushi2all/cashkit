/**
 * Screen 5 — Trace / Explain (SPEC §6-S5, §5-F3).
 *
 * Two levels, because SPEC §5-F3 asks for both:
 *
 *  * **Month level** — the receipt for one month: the opening figure, one
 *    leader-dot row per contributing item with its `item:<id>` meta, and the
 *    closing total. Every figure is selected from the state payload by index;
 *    none is summed here. The opening row is the previous month's closing, or
 *    the book's opening balance for the first month — a different element of
 *    the same series, not a computation.
 *  * **Row level** — tapping a row calls `trace()` for that item and period and
 *    shows the engine's own steps, bindings and rounding. A zero or absent
 *    figure routes to `why_zero()` instead, cause and suggested fix verbatim
 *    (R8). This is where `exact` belongs: the 4dp value, in full, because full
 *    precision is the point of the screen (D-MLP-06).
 *
 * **Reproduce.** SPEC §6-S5 maps it to the SDK's `reproduce()`, which the
 * service does not expose — no endpoint carries it (escalated as D-MLP-46).
 * The app-layer stand-in re-asks the service for the same figure and compares
 * the returned `exact` string with the one on screen, byte for byte. That is a
 * real reproduction check — it catches a figure that has moved — and it is
 * honest about being a narrower one than `reproduce()`.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ScrollView, Text, View, StyleSheet } from "react-native";

import type { BookState, TraceResponse } from "@cashkit/api-types";

import { api, describeError } from "../api/client";
import { exactOf, formatMoney, moneyTone } from "../money/money";
import { Button, Card, Divider, LeaderRow, Stamp } from "../ui/atoms";
import { AsOfLine, DiagnosticList, EnginePanel, monthLabel, shortDate, WhatIfStamp } from "../ui/provenance";
import { EmptyState, ErrorState, LoadingState } from "../ui/states";
import { color, font, space } from "../ui/tokens";

const ZERO = new Set(["0.0000", "-0.0000"]);

type Verdict = "unknown" | "checking" | "reproduced" | "moved" | "unavailable";

export function TraceScreen({
  period,
  scenario,
  onBack,
  testID = "trace-screen",
}: {
  period: string;
  scenario?: string;
  onBack: () => void;
  testID?: string;
}) {
  const [state, setState] = useState<BookState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TraceResponse | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<Verdict>("unknown");

  const load = useCallback(async () => {
    setLoading(true);
    const { data, error: err, response } = await api.GET("/book/state", {
      params: { query: scenario ? { scenario } : {} },
    });
    if (err || !data) {
      setState(null);
      setError(describeError(err, response.status));
    } else {
      setState(data);
      setError(null);
    }
    setLoading(false);
  }, [scenario]);

  useEffect(() => {
    void load();
  }, [load]);

  const index = useMemo(
    () => (state ? state.months.findIndex((m) => m.slice(0, 7) === period.slice(0, 7)) : -1),
    [state, period],
  );

  const openTrace = useCallback(
    async (itemId: string) => {
      setSelected(itemId);
      setDetail(null);
      setDetailError(null);
      setVerdict("unknown");
      const { data, error: err, response } = await api.GET("/book/trace", {
        params: {
          query: { item: itemId, period, measure: "cash", ...(scenario ? { scenario } : {}) },
        },
      });
      if (err || !data) {
        setDetailError(describeError(err, response.status));
        return;
      }
      setDetail(data);
    },
    [period, scenario],
  );

  /** Ask the engine for the same figure again and compare the strings. */
  const reproduce = useCallback(async () => {
    if (!detail || !selected) return;
    setVerdict("checking");
    const { data, error: err } = await api.GET("/book/trace", {
      params: {
        query: { item: selected, period, measure: "cash", ...(scenario ? { scenario } : {}) },
      },
    });
    if (err || !data) {
      setVerdict("unavailable");
      return;
    }
    setVerdict(data.trace.value.exact === detail.trace.value.exact ? "reproduced" : "moved");
  }, [detail, selected, period, scenario]);

  if (loading && !state) return <LoadingState label="Reading the book…" />;
  if (error) return <ErrorState message={error} onRetry={() => void load()} testID={`${testID}-error`} />;
  if (!state) return <EmptyState title="Nothing to trace yet." example="I pay 900 rent every month" />;

  if (index < 0) {
    return (
      <View style={styles.screen}>
        <Text testID={`${testID}-back`} style={styles.crumb} onPress={onBack}>
          ‹ TRACE
        </Text>
        <EmptyState
          title={`${monthLabel(period)} is outside this book's horizon.`}
          example="show me the forecast"
          testID={`${testID}-out-of-range`}
        />
      </View>
    );
  }

  const closing = state.closing[index] ?? null;
  const opening = index === 0 ? state.book.opening_balance : (state.closing[index - 1] ?? null);
  const contributors = state.items
    .map((item) => ({ item, value: item.cash[index] ?? null }))
    .filter((row) => row.value !== null && !ZERO.has(row.value.exact));

  return (
    <View testID={testID} style={styles.screen}>
      <Text testID={`${testID}-back`} style={styles.crumb} onPress={onBack}>
        ‹ TRACE
      </Text>

      <Text testID={`${testID}-question`} style={styles.question}>
        Why{" "}
        <Text style={{ color: color[moneyTone(closing)] }}>{formatMoney(closing)}</Text> at the end of{" "}
        {monthLabel(period)}?
      </Text>
      <AsOfLine
        asOf={state.as_of}
        scenario={state.scenario}
        prefix={monthLabel(period)}
        testID={`${testID}-subline`}
      />
      {/* SPEC §2.4: a trace of a fork, or of a book with uncommitted changes,
          is not base committed state, so it carries the stamp like any other
          hypothetical figure. The payload's own `what_if` decides. */}
      <WhatIfStamp whatIf={state.what_if} testID={`${testID}-what-if`} />

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        <Card testID={`${testID}-receipt`}>
          <LeaderRow
            testID={`${testID}-opening`}
            label={index === 0 ? "Opening balance" : `Closing · ${monthLabel(state.months[index - 1] ?? "")}`}
            value={formatMoney(opening)}
            tone={moneyTone(opening)}
          />
          <Divider />

          {contributors.length === 0 ? (
            <Stamp testID={`${testID}-no-rows`}>NOTHING MOVES IN THIS MONTH</Stamp>
          ) : null}

          {contributors.map(({ item, value }) => (
            <LeaderRow
              key={item.id}
              testID={`${testID}-row-${item.id}`}
              label={item.name}
              meta={`item:${item.id} · ${item.kind}${item.direction ? ` · ${item.direction}` : ""}`}
              value={formatMoney(value)}
              tone={moneyTone(value)}
              emphasis
              onPress={() => void openTrace(item.id)}
            />
          ))}

          <Divider strong />
          <LeaderRow
            testID={`${testID}-total`}
            label={`Closing balance · ${monthLabel(period)}`}
            value={formatMoney(closing)}
            tone={moneyTone(closing)}
            emphasis
          />
        </Card>

        {detailError ? <ErrorState message={detailError} testID={`${testID}-detail-error`} /> : null}

        {detail ? (
          <Card testID={`${testID}-detail`}>
            <Stamp tone="sub">{`TRACE · ${detail.trace.item_id} · ${detail.measure.toUpperCase()}`}</Stamp>
            <LeaderRow
              testID={`${testID}-detail-value`}
              label={detail.trace.item_name}
              meta={`${detail.trace.kind} · period ${shortDate(detail.trace.period_start)} → ${shortDate(
                detail.trace.period_end,
              )}`}
              value={formatMoney(detail.trace.value)}
              tone={moneyTone(detail.trace.value)}
              emphasis
            />
            {/* Full precision belongs here and only here. */}
            <Stamp testID={`${testID}-detail-exact`}>EXACT {exactOf(detail.trace.value)}</Stamp>

            {detail.trace.formula ? (
              <Stamp testID={`${testID}-detail-formula`} tone="sub">
                {detail.trace.formula}
              </Stamp>
            ) : null}

            {detail.trace.steps.length > 0 ? (
              <>
                <Divider />
                {detail.trace.steps.map((step, i) => (
                  <View key={`${step.operation}-${i}`} style={styles.step}>
                    <Text style={styles.stepExpression}>{step.expression}</Text>
                    <View style={styles.stepMeta}>
                      <Stamp tone="faint">{`${step.operation.toUpperCase()} · ${step.rounding.toUpperCase()}`}</Stamp>
                      <Stamp tone="ink">{step.value.exact}</Stamp>
                    </View>
                  </View>
                ))}
              </>
            ) : null}

            {detail.trace.bindings.length > 0 ? (
              <>
                <Divider />
                {detail.trace.bindings.map((binding, i) => (
                  <LeaderRow
                    key={`${binding.symbol}-${i}`}
                    testID={`${testID}-binding-${i}`}
                    label={binding.symbol}
                    meta={`${binding.kind} · ${binding.source}${binding.detail ? ` · ${binding.detail}` : ""}`}
                    value={formatMoney(binding.value)}
                    tone={moneyTone(binding.value)}
                  />
                ))}
              </>
            ) : null}

            {detail.trace.notes.length > 0 ? (
              <Text testID={`${testID}-narrative`} style={styles.narrative}>
                {detail.trace.notes.join(" ")}
              </Text>
            ) : null}

            <DiagnosticList diagnostics={detail.trace.diagnostics} testID={`${testID}-detail-diagnostics`} />

            <Divider />
            <Button
              label={verdict === "checking" ? "Reproducing…" : "Reproduce this figure"}
              testID={`${testID}-reproduce`}
              disabled={verdict === "checking"}
              onPress={() => void reproduce()}
            />
            {verdict !== "unknown" && verdict !== "checking" ? (
              <Stamp
                testID={`${testID}-reproduce-verdict`}
                tone={verdict === "reproduced" ? "pine" : "rust"}
              >
                {verdict === "reproduced"
                  ? `REPRODUCED · ${exactOf(detail.trace.value)} · ENGINE v${detail.engine_version}`
                  : verdict === "moved"
                    ? "THE ENGINE NO LONGER PRODUCES THIS FIGURE"
                    : "COULD NOT RE-ASK THE ENGINE"}
              </Stamp>
            ) : null}
          </Card>
        ) : null}

        <EnginePanel
          engineVersion={state.engine_version ?? "1"}
          revision={state.revision}
          testID={`${testID}-engine-panel`}
        />
      </ScrollView>
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
  crumb: { fontFamily: font.mono, fontSize: 10, letterSpacing: 0.7, color: color.sub },
  question: { fontFamily: font.display, fontSize: 26, fontWeight: "600", color: color.ink },
  body: { flex: 1, marginTop: 10 },
  bodyContent: { gap: 14, paddingBottom: 16 },
  step: { gap: 3, width: "100%" },
  stepExpression: { fontFamily: font.mono, fontSize: 11, color: color.ink },
  stepMeta: { flexDirection: "row", justifyContent: "space-between", width: "100%" },
  narrative: { fontFamily: font.ui, fontSize: 13, color: color.sub },
});
