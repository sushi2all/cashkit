/**
 * Screens 9 and 11 — Item, recurring and custom sequence (SPEC §6-S9, §6-S11).
 *
 * One shell, the variants the type selector navigates between. The selector is
 * navigational and not a mutator, exactly as SPEC §6-S9 says: choosing CUSTOM
 * shows the explicit dates this item produced, it does not convert anything.
 *
 * **What is on this screen, and where it comes from.** No endpoint exposes an
 * item's authored configuration — the segment list, the recurrence, the
 * escalation, the schedule (D-MLP-66). What `GET /book/trace` does expose is
 * the engine's own account of each figure: the segment it came from, how that
 * segment repeats, the escalation applied, and the arithmetic. So the rule and
 * segment cards are assembled from those statements (`itemRule.ts`), every
 * phrase quoted whole and every figure passed through untouched, and the screen
 * says plainly that it can only see inside the horizon.
 *
 * **The edit affordances are the ADR-0013 cell taxonomy.** A generated,
 * segment-backed row shows its arithmetic and offers the two real edits —
 * change the segment amount from a date (M2), or turn this period into a
 * one-off (M5). A schedule date can be added (`edit_schedule_date`). Every one
 * of them is a proposal through `POST /book/edits`; none of them writes.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ScrollView, Text, TextInput, View, StyleSheet } from "react-native";

import type { ItemSeries, TraceResponse } from "@cashkit/api-types";

import { api, describeError } from "../api/client";
import { formatMoney, moneyTone } from "../money/money";
import { useBook } from "../state/book";
import { useEditProposal } from "../state/edits";
import { Button, Card, Divider, LeaderRow, Stamp } from "../ui/atoms";
import { AsOfLine, DiagnosticList, monthLabel, shortDate, WhatIfStamp } from "../ui/provenance";
import { EmptyState, ErrorState, LoadingState } from "../ui/states";
import { color, font, radius, space } from "../ui/tokens";
import { ProposalCard } from "./components/ProposalCard";
import { assembleRule, segmentWindow, type ItemRule } from "./itemRule";

const ZERO = new Set(["0.0000", "-0.0000"]);
const DECIMAL = /^-?\d+(\.\d{1,4})?$/;
/** Enough periods to show a rule without turning a screen into a crawl. */
const MAX_TRACES = 24;

type Variant = "recurring" | "one-off" | "custom";

export function ItemScreen({
  itemId,
  scenario,
  onBack,
  onOpenEvents,
  onOpenTrace,
  testID = "item-screen",
}: {
  itemId: string;
  scenario?: string;
  onBack: () => void;
  /** The ONE-OFF tab: the ledger rows carrying this item (screen 10). */
  onOpenEvents: (itemId: string) => void;
  onOpenTrace: (period: string, scenario: string) => void;
  testID?: string;
}) {
  const book = useBook();
  const [item, setItem] = useState<ItemSeries | null>(null);
  const [rule, setRule] = useState<ItemRule | null>(null);
  const [months, setMonths] = useState<string[]>([]);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [revision, setRevision] = useState<string | null>(null);
  const [whatIf, setWhatIf] = useState<TraceResponse["what_if"] | null>(null);
  const [resolvedScenario, setResolvedScenario] = useState<string>("base");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [variant, setVariant] = useState<Variant>("recurring");
  const [changeFrom, setChangeFrom] = useState("");
  const [changeAmount, setChangeAmount] = useState("");
  const [newDate, setNewDate] = useState("");
  const [newAmount, setNewAmount] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const query = scenario ? { scenario } : {};
    const state = await api.GET("/book/state", { params: { query } });
    if (state.error || !state.data) {
      setLoading(false);
      setError(describeError(state.error, state.response.status));
      return;
    }
    const found = state.data.items.find((i) => i.id === itemId) ?? null;
    setItem(found);
    setMonths(state.data.months);
    setAsOf(state.data.as_of);
    setRevision(state.data.revision);
    setWhatIf(state.data.what_if);
    setResolvedScenario(state.data.scenario);
    if (!found) {
      setLoading(false);
      setError(null);
      return;
    }

    // Ask the engine about every period this item is non-zero in. Each answer
    // is a statement about one figure; together they are the rule.
    const periods = state.data.months.filter((_, index) => {
      const value = found.cash[index];
      return value != null && !ZERO.has(value.exact);
    });
    const traces = await Promise.all(
      periods.slice(0, MAX_TRACES).map((period) =>
        api.GET("/book/trace", {
          params: { query: { item: itemId, period, measure: "cash", ...query } },
        }),
      ),
    );
    const ok = traces.map((t) => t.data).filter((d): d is TraceResponse => Boolean(d));
    setRule(assembleRule(ok));
    setError(null);
    setLoading(false);
  }, [itemId, scenario]);

  useEffect(() => {
    void load();
  }, [load]);

  const edit = useEditProposal({
    onApplied: async () => {
      await book.refresh();
      await load();
    },
  });

  const changeAmountFromDate = useCallback(async () => {
    // M2 — `set_amount` with `from_date` splits the segment rather than
    // rewriting the past. That is the whole reason the slot exists.
    await edit.propose(
      [
        {
          op: "set_amount",
          item: itemId,
          amount: changeAmount.trim(),
          from_date: changeFrom.trim(),
          ...(scenario ? { scenario } : {}),
        },
      ],
      { origin: "cell_edit", ...(scenario ? { scenario } : {}) },
    );
  }, [edit, itemId, changeAmount, changeFrom, scenario]);

  const addScheduleDate = useCallback(async () => {
    await edit.propose(
      [
        {
          op: "edit_schedule_date",
          item: itemId,
          action: "add",
          date: newDate.trim(),
          amount: newAmount.trim(),
          ...(scenario ? { scenario } : {}),
        },
      ],
      { origin: "cell_edit", ...(scenario ? { scenario } : {}) },
    );
  }, [edit, itemId, newDate, newAmount, scenario]);

  const convertToOneOff = useCallback(
    async (period: string, amount: string) => {
      // M5 — the other branch of the ADR-0013 generated-cell edit: rather than
      // reinterpreting the segment, record a one-off the engine can date.
      await edit.propose(
        [
          {
            op: "add_event",
            date: period,
            amount,
            item: itemId,
            note: `one-off for ${itemId}`,
            ...(scenario ? { scenario } : {}),
          },
        ],
        { origin: "cell_edit", ...(scenario ? { scenario } : {}) },
      );
    },
    [edit, itemId, scenario],
  );

  const occurrences = useMemo(() => {
    if (!item) return [];
    return months
      .map((period, index) => ({ period, value: item.cash[index] ?? null }))
      .filter((row) => row.value !== null && !ZERO.has(row.value.exact));
  }, [item, months]);

  const next = useMemo(
    () => (asOf ? occurrences.filter((o) => o.period >= asOf).slice(0, 4) : []),
    [occurrences, asOf],
  );

  if (loading && !item) return <LoadingState label="Reading the item…" />;
  if (error) return <ErrorState message={error} onRetry={() => void load()} testID={`${testID}-error`} />;
  if (!item) {
    return (
      <View style={styles.screen}>
        <Text testID={`${testID}-back`} style={styles.crumb} onPress={onBack}>
          ‹ ITEM
        </Text>
        <EmptyState
          title={`No item called ${itemId} in this scenario.`}
          example="what do I pay every month?"
          testID={`${testID}-missing`}
        />
      </View>
    );
  }

  const changeReady = DECIMAL.test(changeAmount.trim()) && changeFrom.trim().length > 0;
  const addReady = DECIMAL.test(newAmount.trim()) && newDate.trim().length > 0;

  return (
    <View testID={testID} style={styles.screen}>
      <Text testID={`${testID}-back`} style={styles.crumb} onPress={onBack}>
        ‹ ITEM
      </Text>

      <View style={styles.titleRow}>
        <Text testID={`${testID}-title`} style={styles.title}>
          {item.name}
        </Text>
        {Object.entries(item.tags).map(([key, value]) => (
          <View key={key} style={styles.tag}>
            <Text style={styles.tagLabel}>{`${key}: ${value}`}</Text>
          </View>
        ))}
      </View>

      <Text testID={`${testID}-subline`} style={styles.subline}>
        {`item:${item.id} · SCENARIO ${resolvedScenario.toUpperCase()} · AS-OF ${shortDate(asOf)}`}
      </Text>
      <WhatIfStamp whatIf={whatIf} testID={`${testID}-what-if`} />

      {/* Navigational, not a mutator (SPEC §6-S9). */}
      <View testID={`${testID}-variants`} style={styles.variants}>
        {(["recurring", "one-off", "custom"] as const).map((option) => (
          <Text
            key={option}
            testID={`${testID}-variant-${option}`}
            accessibilityRole="button"
            accessibilityState={{ selected: variant === option }}
            onPress={() => (option === "one-off" ? onOpenEvents(item.id) : setVariant(option))}
            style={[styles.variant, variant === option && styles.variantOn]}
          >
            {option.toUpperCase()}
          </Text>
        ))}
      </View>

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {variant === "recurring" ? (
          <>
            <Card testID={`${testID}-rule-card`}>
              <Stamp tone="sub" testID={`${testID}-rule-label`}>
                RULE · AS THE ENGINE APPLIES IT
              </Stamp>
              {rule && rule.segments.length > 0 ? (
                (() => {
                  const current = rule.segments[rule.segments.length - 1] as (typeof rule.segments)[number];
                  const window = segmentWindow(current);
                  const latest = current.periods[current.periods.length - 1];
                  return (
                    <>
                      <LeaderRow
                        testID={`${testID}-rule-amount`}
                        label="Amount"
                        value={formatMoney(latest?.amount ?? null)}
                        tone={moneyTone(latest?.amount ?? null)}
                        emphasis
                      />
                      <LeaderRow
                        testID={`${testID}-rule-repeats`}
                        label="Repeats"
                        value={
                          <Text style={styles.engineWords}>{current.recurrence ?? "not reported"}</Text>
                        }
                        tone="sub"
                      />
                      <LeaderRow
                        testID={`${testID}-rule-starts`}
                        label="Starts"
                        value={<Text style={styles.engineWords}>{current.source}</Text>}
                        tone="sub"
                      />
                      <LeaderRow
                        testID={`${testID}-rule-seen`}
                        label="Seen in the horizon"
                        value={
                          <Text style={styles.engineWords}>
                            {window ? `${monthLabel(window.first)} – ${monthLabel(window.last)}` : "—"}
                          </Text>
                        }
                        tone="sub"
                      />
                      <LeaderRow
                        testID={`${testID}-rule-escalation`}
                        label="Escalation"
                        value={<Text style={styles.engineWords}>{current.escalation ?? "none"}</Text>}
                        tone="sub"
                      />
                      {current.steps.length > 0 ? (
                        <>
                          <Divider />
                          {/* The arithmetic ADR-0013 requires a generated cell
                              to show, in the engine's own expressions. */}
                          {current.steps.map((step, index) => (
                            <View key={`${step.operation}-${index}`} style={styles.step}>
                              <Text testID={`${testID}-step-${index}`} style={styles.stepExpression}>
                                {step.expression}
                              </Text>
                              <View style={styles.stepMeta}>
                                <Stamp tone="faint">
                                  {`${step.operation.toUpperCase()} · ${step.rounding.toUpperCase()}`}
                                </Stamp>
                                <Stamp tone="ink">{step.value.exact}</Stamp>
                              </View>
                            </View>
                          ))}
                        </>
                      ) : null}
                    </>
                  );
                })()
              ) : (
                <Stamp testID={`${testID}-rule-none`}>
                  THE ENGINE GENERATES NOTHING FOR THIS ITEM INSIDE THE HORIZON
                </Stamp>
              )}

              {next.length > 0 ? (
                <Stamp testID={`${testID}-next`}>
                  {`NEXT: ${next
                    .map((o) => `${monthLabel(o.period)} ${formatMoney(o.value)}`)
                    .join(" · ")}`}
                </Stamp>
              ) : null}
            </Card>

            <Card testID={`${testID}-segments-card`}>
              <Stamp tone="sub" testID={`${testID}-segments-label`}>
                SEGMENTS · CHANGES KEEP HISTORY
              </Stamp>
              {(rule?.segments ?? []).map((segment, index) => {
                const window = segmentWindow(segment);
                const sample = segment.periods[segment.periods.length - 1];
                return (
                  <LeaderRow
                    key={`${segment.source}-${index}`}
                    testID={`${testID}-segment-${index}`}
                    label={segment.source}
                    meta={
                      window
                        ? `${monthLabel(window.first)} – ${monthLabel(window.last)} · ${
                            segment.periods.length
                          } occurrences`
                        : undefined
                    }
                    value={formatMoney(sample?.amount ?? null)}
                    tone={moneyTone(sample?.amount ?? null)}
                    emphasis={index === (rule?.segments.length ?? 0) - 1}
                  />
                );
              })}
              {/* The honest limit of assembling a rule from traces. */}
              <Stamp testID={`${testID}-segments-caveat`}>
                READ FROM THE ENGINE&apos;S OWN TRACE · ONLY SEGMENTS ACTIVE INSIDE THE HORIZON APPEAR
              </Stamp>
            </Card>

            <Card testID={`${testID}-change-card`}>
              <Stamp tone="sub">CHANGE AMOUNT FROM A DATE…</Stamp>
              <Text style={styles.explainer}>
                A change from a date splits the segment. What was already paid stays as it was.
              </Text>
              <View style={styles.inline}>
                <TextInput
                  testID={`${testID}-change-from`}
                  accessibilityLabel="Change from this date"
                  style={styles.input}
                  placeholder="2026-09-01"
                  placeholderTextColor={color.faint}
                  value={changeFrom}
                  onChangeText={setChangeFrom}
                />
                <TextInput
                  testID={`${testID}-change-amount`}
                  accessibilityLabel="New amount"
                  style={styles.input}
                  placeholder="-1200.00"
                  placeholderTextColor={color.faint}
                  value={changeAmount}
                  onChangeText={setChangeAmount}
                />
              </View>
              <Button
                label="Propose the change"
                variant="primary"
                testID={`${testID}-change-submit`}
                disabled={edit.busy || !changeReady}
                onPress={() => void changeAmountFromDate()}
              />
            </Card>
          </>
        ) : null}

        {variant === "custom" ? (
          <>
            <Card testID={`${testID}-dates-card`}>
              <Stamp tone="sub" testID={`${testID}-dates-label`}>
                DATES · EVERY OCCURRENCE INSIDE THE HORIZON
              </Stamp>
              {occurrences.length === 0 ? (
                <Stamp testID={`${testID}-dates-none`}>NOTHING FALLS INSIDE THE HORIZON</Stamp>
              ) : (
                occurrences.map((row, index) => (
                  <LeaderRow
                    key={row.period}
                    testID={`${testID}-date-${row.period.slice(0, 7)}`}
                    label={monthLabel(row.period)}
                    meta={`occurrence ${index + 1} of ${occurrences.length}`}
                    value={formatMoney(row.value)}
                    tone={moneyTone(row.value)}
                    onPress={() => onOpenTrace(row.period, resolvedScenario)}
                  />
                ))
              )}
            </Card>

            <Card testID={`${testID}-add-date-card`}>
              <Stamp tone="sub">+ ADD A DATE</Stamp>
              <View style={styles.inline}>
                <TextInput
                  testID={`${testID}-add-date`}
                  accessibilityLabel="Date to add"
                  style={styles.input}
                  placeholder="2027-01-15"
                  placeholderTextColor={color.faint}
                  value={newDate}
                  onChangeText={setNewDate}
                />
                <TextInput
                  testID={`${testID}-add-amount`}
                  accessibilityLabel="Amount on that date"
                  style={styles.input}
                  placeholder="-800.00"
                  placeholderTextColor={color.faint}
                  value={newAmount}
                  onChangeText={setNewAmount}
                />
              </View>
              <Button
                label="Propose the date"
                testID={`${testID}-add-submit`}
                disabled={edit.busy || !addReady}
                onPress={() => void addScheduleDate()}
              />
              {next[0] ? (
                <Button
                  label={`Turn ${monthLabel(next[0].period)} into a one-off instead`}
                  testID={`${testID}-convert`}
                  disabled={edit.busy}
                  onPress={() =>
                    void convertToOneOff(next[0]!.period, next[0]!.value?.exact ?? "0")
                  }
                />
              ) : null}
            </Card>
          </>
        ) : null}

        {edit.error ? <ErrorState message={edit.error} testID={`${testID}-edit-error`} /> : null}
        {edit.pending ? (
          <ProposalCard
            proposal={edit.pending}
            busy={edit.busy}
            testID={`${testID}-proposal-card`}
            onApply={() => void edit.resolve("accept")}
            onDiscard={() => void edit.resolve("discard")}
            onEdit={() => undefined}
          />
        ) : null}
        {edit.resolution && edit.resolution.kind !== "refreshed" ? (
          <Stamp
            testID={`${testID}-resolution`}
            tone={edit.resolution.kind === "applied" ? "pine" : "faint"}
          >
            {edit.resolution.kind === "applied"
              ? `APPLIED · REV ${edit.resolution.revision ? edit.resolution.revision.slice(0, 7) : "UNCOMMITTED"}`
              : "DISCARDED"}
          </Stamp>
        ) : null}
        {edit.pending ? <DiagnosticList diagnostics={edit.pending.diagnostics} /> : null}

        <Card testID={`${testID}-provenance`}>
          <Stamp tone="sub">PROVENANCE</Stamp>
          <LeaderRow
            label="Item"
            value={<Text style={styles.engineWords}>{`item:${item.id}`}</Text>}
            tone="sub"
          />
          <LeaderRow
            label="Kind"
            value={
              <Text style={styles.engineWords}>
                {`${item.kind}${item.direction ? ` · ${item.direction}` : ""}${
                  item.formula ? " · derived" : ""
                }`}
              </Text>
            }
            tone="sub"
          />
          <LeaderRow
            label="Book revision"
            value={
              <Text style={styles.engineWords}>{revision ? revision.slice(0, 7) : "UNCOMMITTED"}</Text>
            }
            tone="sub"
          />
          {/* Per-item change attribution is not on the wire (D-MLP-66). Say so
              rather than show a commit that may have nothing to do with it. */}
          <Stamp testID={`${testID}-provenance-caveat`}>
            THE REVISION THAT CREATED OR LAST CHANGED THIS ITEM IS NOT EXPOSED
          </Stamp>
        </Card>
      </ScrollView>

      <View style={styles.footer}>
        <AsOfLine asOf={asOf ?? ""} scenario={resolvedScenario} testID={`${testID}-as-of`} />
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
  crumb: { fontFamily: font.mono, fontSize: 10, letterSpacing: 0.7, color: color.sub },
  titleRow: { flexDirection: "row", alignItems: "center", gap: 10, flexWrap: "wrap" },
  title: { fontFamily: font.display, fontSize: 28, fontWeight: "600", color: color.ink },
  tag: { backgroundColor: color.pineTint, borderRadius: 999, paddingVertical: 5, paddingHorizontal: 11 },
  tagLabel: { fontFamily: font.mono, fontSize: 9, color: color.pine },
  subline: { fontFamily: font.mono, fontSize: 9.5, letterSpacing: 0.6, color: color.sub },
  variants: { flexDirection: "row", gap: 8 },
  variant: {
    fontFamily: font.mono,
    fontSize: 9,
    letterSpacing: 0.7,
    color: color.sub,
    borderWidth: 1,
    borderColor: color.hair,
    borderRadius: radius.pill,
    paddingVertical: 7,
    paddingHorizontal: 12,
    backgroundColor: color.card,
  },
  variantOn: { color: color.pine, borderColor: color.pine, backgroundColor: color.pineTint },
  body: { flex: 1 },
  bodyContent: { gap: 14, paddingBottom: 12 },
  engineWords: { fontFamily: font.mono, fontSize: 10, color: color.ink },
  explainer: { fontFamily: font.ui, fontSize: 12, color: color.sub },
  step: { gap: 3, width: "100%" },
  stepExpression: { fontFamily: font.mono, fontSize: 10.5, color: color.ink },
  stepMeta: { flexDirection: "row", justifyContent: "space-between", width: "100%" },
  inline: { flexDirection: "row", gap: 10, width: "100%" },
  input: {
    flex: 1,
    height: 42,
    borderRadius: radius.card,
    borderWidth: 1,
    borderColor: color.hair,
    backgroundColor: color.card,
    paddingHorizontal: 12,
    fontFamily: font.ui,
    fontSize: 14,
    color: color.ink,
    outlineStyle: "none",
  } as object,
  footer: { gap: 6 },
});
