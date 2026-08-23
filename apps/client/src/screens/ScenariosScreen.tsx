/**
 * Screen 6 — Scenarios and compare (SPEC §6-S6, §5-F4).
 *
 * This is the first surface in the app where a fork is genuinely active, and
 * therefore the first place SPEC §2.4 has real work to do. The rule, verbatim:
 *
 * > Base is the plan of record. Any figure NOT from the committed state of
 * > `base` — a non-base scenario (active or not), a throwaway overlay, or a
 * > dry-run including pending changes — carries the WHAT-IF stamp: payload
 * > field `what_if: {stamped: true, reason: "scenario"|"overlay"|"pending",
 * > scenario?: id}`, and a rendered stamp element (ADR-0024). The Home header
 * > and sparkline always show base committed figures, in neutral form, even
 * > while a fork is active; a fork's own figures render stamped with the
 * > fork's name.
 *
 * The compare table holds one column per scenario, so the stamp has to be **per
 * column** — a fork's column is hypothetical while base's column, on a clean
 * book, is the plan of record. That distinction is not re-derived here: each
 * column's stamp is the `what_if` the service itself put on
 * `GET /book/state?scenario=<id>` for that scenario. The client implements no
 * copy of the rule, which is how S3 kept the Home header honest and how this
 * screen stays honest as scenarios multiply.
 *
 * Activation is app state, not book content, and it supersedes every pending
 * proposal (SPEC §2.5) — so the pending card is cleared when it happens.
 * Creating a fork is a write like any other: `POST /book/scenarios` returns a
 * proposal, never a scenario (D-MLP-14).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ScrollView, Text, TextInput, View, StyleSheet } from "react-native";

import type { CompareResponse, Money, Scenario, WhatIf } from "@cashkit/api-types";

import { api, describeError } from "../api/client";
import { asDiagnostics } from "../api/diagnostics";
import { formatBare, isNegative, moneyTone } from "../money/money";
import { useBook } from "../state/book";
import { useConversation } from "../state/conversation";
import { useEditProposal } from "../state/edits";
import { Button, Card, Divider, Stamp } from "../ui/atoms";
import { AsOfLine, DiagnosticList, monthLabel, shortDate, WhatIfStamp } from "../ui/provenance";
import { EmptyState, ErrorState, LoadingState } from "../ui/states";
import { color, font, space } from "../ui/tokens";
import { AnswerCard } from "./components/AnswerCard";
import { CompareChart } from "./components/CompareChart";
import { MicButton } from "./components/MicButton";
import { ProposalCard } from "./components/ProposalCard";

const BASE = "base";

/** The first period where the two columns stop agreeing. A string test. */
function firstDivergence(compare: CompareResponse): number {
  return compare.periods.findIndex((period) => {
    const figures = compare.scenarios.map((id) => period.values[id] ?? null);
    const first = figures[0] ?? null;
    return figures.some((figure) => {
      // Absent and zero are different figures, and a scenario that has one
      // where the other has the second has already diverged (SPEC §5-F4).
      if (figure === null || first === null) return figure !== first;
      return figure.exact !== first.exact;
    });
  });
}

export function ScenariosScreen({
  onOpenTrace,
  onBack,
  testID = "scenarios-screen",
}: {
  onOpenTrace: (period: string, scenario: string) => void;
  onBack: () => void;
  testID?: string;
}) {
  const book = useBook();
  const conversation = useConversation();

  const [scenarios, setScenarios] = useState<Scenario[] | null>(null);
  const [active, setActive] = useState<string>(BASE);
  const [selected, setSelected] = useState<string[]>([]);
  const [compare, setCompare] = useState<CompareResponse | null>(null);
  /** The service's own `what_if` for each selected scenario, by id. */
  const [stamps, setStamps] = useState<Record<string, WhatIf>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);

  const edit = useEditProposal({
    onApplied: async () => {
      await book.refresh();
      await loadScenarios();
    },
  });

  const loadScenarios = useCallback(async () => {
    const { data, error: err, response } = await api.GET("/book/scenarios", {});
    if (err || !data) {
      setScenarios(null);
      setError(describeError(err, response.status));
      return null;
    }
    setScenarios(data.scenarios);
    setActive(data.active);
    setError(null);
    return data;
  }, []);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      const data = await loadScenarios();
      if (data) {
        // Default the comparison to the plan of record against whatever fork
        // is in play — the question this screen exists to answer.
        const forks = data.scenarios.filter((s) => !s.is_base);
        const other = forks.find((s) => s.id === data.active) ?? forks[0];
        setSelected(other ? [BASE, other.id] : [BASE]);
      }
      setLoading(false);
    })();
  }, [loadScenarios]);

  /** Compare, plus the authoritative per-scenario stamp for each column. */
  const loadCompare = useCallback(async (ids: string[]) => {
    if (ids.length < 2) {
      setCompare(null);
      setStamps({});
      return;
    }
    const [comparison, ...states] = await Promise.all([
      api.GET("/book/compare", { params: { query: { scenarios: ids.join(","), metric: "cash" } } }),
      ...ids.map((id) => api.GET("/book/state", { params: { query: { scenario: id } } })),
    ]);
    if (comparison.error || !comparison.data) {
      setCompare(null);
      setError(describeError(comparison.error, comparison.response.status));
      return;
    }
    setCompare(comparison.data);
    setError(null);
    const next: Record<string, WhatIf> = {};
    states.forEach((state, index) => {
      const id = ids[index];
      if (id && state.data) next[id] = state.data.what_if;
    });
    setStamps(next);
  }, []);

  useEffect(() => {
    void loadCompare(selected);
  }, [selected, loadCompare]);

  const toggleSelected = useCallback((id: string) => {
    setSelected((current) => {
      if (current.includes(id)) {
        const next = current.filter((s) => s !== id);
        return next.length >= 1 ? next : current;
      }
      // Two columns is what the compare view shows (SPEC §5-F4); a third
      // replaces the second rather than crowding the table.
      return current.length >= 2 ? [current[0] as string, id] : [...current, id];
    });
  }, []);

  const activate = useCallback(
    async (id: string) => {
      setBusy(true);
      const { error: err, response } = await api.POST("/book/scenarios/{scenario_id}/activate", {
        params: { path: { scenario_id: id } },
      });
      setBusy(false);
      if (err) {
        setError(describeError(err, response.status));
        return;
      }
      // Activation supersedes every pending proposal (SPEC §2.5). A card that
      // is superseded on the service must not stay tappable here.
      edit.reset();
      await loadScenarios();
      await book.refresh();
    },
    [edit, loadScenarios, book],
  );

  const createFork = useCallback(async () => {
    const name = newName.trim();
    if (!name) return;
    await edit.propose([{ op: "fork_scenario", name, note: "created from the Scenarios screen" }], {
      origin: "button",
    });
    setNewName("");
  }, [newName, edit]);

  const divergeIndex = useMemo(() => (compare ? firstDivergence(compare) : -1), [compare]);
  const firstNegativeIndex = useMemo(() => {
    if (!compare) return -1;
    return compare.periods.findIndex((period) =>
      compare.scenarios.some((id) => isNegative(period.values[id] ?? null)),
    );
  }, [compare]);

  const lastTurn = useMemo(() => {
    for (let i = conversation.entries.length - 1; i >= 0; i -= 1) {
      const entry = conversation.entries[i];
      if (entry && entry.kind === "turn") return entry.response;
    }
    return null;
  }, [conversation.entries]);

  if (loading && !scenarios) return <LoadingState label="Reading your scenarios…" />;
  if (error && !scenarios) {
    return <ErrorState message={error} onRetry={() => void loadScenarios()} testID={`${testID}-error`} />;
  }
  if (!scenarios || !book.state) {
    return (
      <View style={styles.screen}>
        <EmptyState
          title="No book to fork yet."
          example="I earn 3,000 a month and pay 900 rent"
          testID={`${testID}-empty`}
        />
      </View>
    );
  }

  const state = book.state;
  const compareDiagnostics = asDiagnostics(compare?.diagnostics);
  const firstNegativeCode = compareDiagnostics[0]?.code ?? null;

  return (
    <View testID={testID} style={styles.screen}>
      <View style={styles.headerRow}>
        <Text testID={`${testID}-back`} style={styles.back} onPress={onBack}>
          ‹ BACK
        </Text>
        <Text style={styles.title}>Scenarios</Text>
      </View>

      <Text testID={`${testID}-subline`} style={styles.subline}>
        {`COMPARE · SAME BOOK · AS-OF ${shortDate(state.as_of)}`}
      </Text>

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        <View testID={`${testID}-chips`} style={styles.chips}>
          {scenarios.map((scenario) => {
            const picked = selected.includes(scenario.id);
            return (
              <Text
                key={scenario.id}
                testID={`${testID}-chip-${scenario.id}`}
                accessibilityRole="button"
                onPress={() => toggleSelected(scenario.id)}
                style={[styles.chip, picked && styles.chipPicked]}
              >
                {`${picked ? "✓ " : ""}${scenario.id}${scenario.is_active ? " ·" : ""}`}
              </Text>
            );
          })}
        </View>

        <View style={styles.newRow}>
          <TextInput
            testID={`${testID}-new-name`}
            accessibilityLabel="Name for the new scenario"
            style={styles.newInput}
            placeholder="+ New scenario…"
            placeholderTextColor={color.faint}
            value={newName}
            onChangeText={setNewName}
            onSubmitEditing={() => void createFork()}
          />
          <View style={styles.newButton}>
            <Button
              label="Create"
              testID={`${testID}-new-create`}
              disabled={edit.busy || newName.trim().length === 0}
              onPress={() => void createFork()}
            />
          </View>
        </View>

        {/* Activation is app state, not book content, so it needs no card. */}
        <View style={styles.activateRow}>
          <Stamp testID={`${testID}-active`}>{`ACTIVE · ${active.toUpperCase()}`}</Stamp>
          {scenarios
            .filter((scenario) => scenario.id !== active)
            .map((scenario) => (
              <Text
                key={scenario.id}
                testID={`${testID}-activate-${scenario.id}`}
                accessibilityRole="button"
                onPress={() => void activate(scenario.id)}
                style={[styles.activateLink, busy && styles.dim]}
              >
                {`WORK IN ${scenario.id.toUpperCase()} ›`}
              </Text>
            ))}
        </View>

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

        {compare && compare.periods.length > 0 ? (
          <>
            <Card testID={`${testID}-chart-card`}>
              <CompareChart
                months={compare.periods.map((p) => p.period_start)}
                series={compare.scenarios.map((id) => ({
                  scenario: id,
                  values: compare.periods.map((p) => p.values[id] ?? null),
                }))}
                divergePeriod={
                  divergeIndex >= 0 ? (compare.periods[divergeIndex]?.period_start ?? null) : null
                }
                divergeDelta={divergeIndex >= 0 ? (compare.periods[divergeIndex]?.delta ?? null) : null}
                testID={`${testID}-chart`}
              />
            </Card>

            <Card testID={`${testID}-table-card`} style={styles.tableCard}>
              <View style={styles.headRow}>
                <View style={styles.cell}>
                  <Text style={styles.headCell}>MONTH</Text>
                </View>
                {compare.scenarios.map((id) => (
                  <View key={id} style={styles.cell}>
                    <Text style={styles.headCell}>{`${id.toUpperCase()} END`}</Text>
                    {/* SPEC §2.4, per column: the service's own stamp for this
                        scenario, never a rule re-derived on the client. */}
                    <WhatIfStamp whatIf={stamps[id]} testID={`${testID}-column-what-if-${id}`} />
                  </View>
                ))}
                <View style={styles.cell}>
                  <Text style={styles.headCell}>Δ</Text>
                </View>
              </View>

              {compare.periods.map((period, index) => {
                const key = period.period_start.slice(0, 7);
                return (
                  <View key={period.period_start}>
                    <Divider />
                    <Text
                      testID={`${testID}-row-${key}`}
                      accessibilityRole="button"
                      onPress={() => onOpenTrace(period.period_start, compare.scenarios[0] ?? BASE)}
                      style={styles.rowPress}
                    >
                      <View style={styles.dataRow}>
                        <View style={styles.cell}>
                          <Text style={styles.cellMonth}>{monthLabel(period.period_start)}</Text>
                        </View>
                        {compare.scenarios.map((id) => {
                          const value: Money | null = period.values[id] ?? null;
                          return (
                            <View key={id} style={styles.cell}>
                              {/* Absent is not zero: a scenario with no figure
                                  for a period renders a dash, never 0.00. */}
                              <Text
                                testID={`${testID}-row-${key}-${id}`}
                                style={[styles.cellValue, { color: color[moneyTone(value)] }]}
                              >
                                {formatBare(value)}
                              </Text>
                            </View>
                          );
                        })}
                        <View style={styles.cell}>
                          <Text
                            testID={`${testID}-row-${key}-delta`}
                            style={[styles.cellValue, { color: color.sub }]}
                          >
                            {formatBare(period.delta ?? null)}
                          </Text>
                        </View>
                      </View>
                    </Text>
                    {index === firstNegativeIndex ? (
                      <View style={styles.noteRow}>
                        <Stamp tone="rust" testID={`${testID}-first-negative`}>
                          {`FIRST NEGATIVE · ${monthLabel(period.period_start)}${
                            firstNegativeCode ? ` · ${firstNegativeCode}` : ""
                          }`}
                        </Stamp>
                      </View>
                    ) : null}
                  </View>
                );
              })}
            </Card>

            <DiagnosticList
              diagnostics={compareDiagnostics}
              testID={`${testID}-compare-diagnostics`}
            />
          </>
        ) : (
          <EmptyState
            title="Pick two scenarios to compare."
            example="what if I buy a car in November?"
            testID={`${testID}-no-compare`}
          />
        )}

        {lastTurn ? <AnswerCard turn={lastTurn} testID={`${testID}-answer-card`} /> : null}
      </ScrollView>

      <View style={styles.footer}>
        <View style={styles.footerRow}>
          <Stamp testID={`${testID}-footer-note`}>SAME BOOK · ONE CHANGE · TAP A ROW FOR THE TRACE</Stamp>
          <MicButton
            testID={`${testID}-mic`}
            disabled={conversation.busy}
            onTranscript={(text) =>
              void conversation.ask(text, { scenario: selected[1] ?? selected[0] ?? BASE })
            }
          />
        </View>
        <AsOfLine asOf={state.as_of} scenario={active} testID={`${testID}-as-of`} />
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
  subline: { fontFamily: font.mono, fontSize: 9.5, letterSpacing: 0.6, color: color.sub },
  body: { flex: 1 },
  bodyContent: { gap: 14, paddingBottom: 12 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    fontFamily: font.ui,
    fontSize: 13,
    color: color.sub,
    borderWidth: 1,
    borderColor: color.hair,
    borderRadius: 999,
    paddingVertical: 7,
    paddingHorizontal: 14,
    backgroundColor: color.card,
  },
  chipPicked: { color: color.pine, borderColor: color.pine, backgroundColor: color.pineTint },
  newRow: { flexDirection: "row", gap: 10, alignItems: "center" },
  newInput: {
    flex: 1,
    height: 44,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: color.hair,
    backgroundColor: color.card,
    paddingHorizontal: 16,
    fontFamily: font.ui,
    fontSize: 14,
    color: color.ink,
    outlineStyle: "none",
  } as object,
  newButton: { width: 110, flexDirection: "row" },
  activateRow: { flexDirection: "row", alignItems: "center", gap: 12, flexWrap: "wrap" },
  activateLink: { fontFamily: font.mono, fontSize: 9.5, letterSpacing: 0.7, color: color.pine },
  dim: { opacity: 0.4 },
  tableCard: { padding: 0, gap: 0 },
  headRow: { flexDirection: "row", paddingHorizontal: 14, paddingTop: 12, paddingBottom: 8 },
  dataRow: { flexDirection: "row", width: "100%" },
  rowPress: { paddingHorizontal: 14, paddingVertical: 10 },
  cell: { flex: 1 },
  headCell: { fontFamily: font.mono, fontSize: 8.5, color: color.faint },
  cellMonth: { fontFamily: font.ui, fontSize: 13, color: color.sub },
  cellValue: { fontFamily: font.ui, fontSize: 13 },
  noteRow: { paddingHorizontal: 14, paddingBottom: 10, backgroundColor: color.pineTint },
  footer: { gap: 6 },
  footerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
});
