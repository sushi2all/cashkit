/**
 * Screen 1 — Home / Chat, and screen 2 — the Alert / negative what-if variant
 * (SPEC §6-S1, §6-S2). Screen 4, the proposal card, is nested here.
 *
 * The header is the part with a rule attached. SPEC §2.4:
 *
 * > The Home header and sparkline always show base committed figures, in
 * > neutral form, even while a fork is active.
 *
 * So the balance row and the sparkline read from `useBook()`, which only ever
 * holds committed state, and never from a turn's answer. A hypothetical answer
 * changes the card below the divider and nothing above it. That separation is
 * the error class ADR-0024 exists to prevent, and it is the reason the two
 * halves of this screen read from two different places.
 *
 * The designed `ON-DEVICE` chip is gone (D-MLP-05a): the eyebrow carries
 * book · scenario · as-of only.
 */
import React, { useCallback, useMemo, useState } from "react";
import { ScrollView, Text, View, StyleSheet } from "react-native";

import type { BookState } from "@cashkit/api-types";

import { api, describeError } from "../api/client";
import { formatMoney, moneyTone } from "../money/money";
import { useBook } from "../state/book";
import { useConversation } from "../state/conversation";
import { Button, Divider, Eyebrow, QuoteRow, Stamp } from "../ui/atoms";
import { AsOfLine, monthLabel, shortDate, WhatIfStamp } from "../ui/provenance";
import { EmptyState, ErrorState, LoadingState } from "../ui/states";
import { color, font, space } from "../ui/tokens";
import { AnswerCard } from "./components/AnswerCard";
import { AskBar } from "./components/AskBar";
import { ProposalCard } from "./components/ProposalCard";
import { Sparkline } from "./components/Sparkline";
import { WarningsBanner } from "./components/WarningsBanner";

/**
 * The figure the balance row shows.
 *
 * There is no "balance today" in the payload: the MLP grain is monthly
 * (SPEC §5-F3), so the honest figure is the closing balance of the month that
 * contains `as_of`, **selected** from the series by index — never interpolated
 * and never re-derived. It is labelled for what it is (D-MLP-43).
 */
function headlineBalance(state: BookState) {
  const month = state.as_of.slice(0, 7);
  const index = state.months.findIndex((m) => m.slice(0, 7) === month);
  if (index >= 0) {
    const value = state.closing[index];
    if (value) return { value, label: `at the end of ${monthLabel(state.months[index] ?? state.as_of)}` };
  }
  return { value: state.summary.closing_balance, label: "at the horizon" };
}

export function HomeScreen({
  onOpenTrace,
  onOpenForecast,
  onOpenScenarios,
  onOpenActuals,
  onOpenPlan,
  onOpenImport,
  onOpenSettings,
  testID = "home-screen",
}: {
  onOpenTrace: (period: string, scenario: string) => void;
  onOpenForecast: () => void;
  /** The rest of the app (SPEC §6-S6…S11, S15). Home is the hub. */
  onOpenScenarios?: () => void;
  onOpenActuals?: () => void;
  onOpenPlan?: () => void;
  onOpenImport?: () => void;
  onOpenSettings?: () => void;
  testID?: string;
}) {
  const book = useBook();
  const conversation = useConversation();
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const state = book.state;

  const save = useCallback(async () => {
    setSaving(true);
    setSaveError(null);
    const result = await book.save("Saved from the app");
    if (!result.ok) setSaveError(result.error ?? "Could not save.");
    setSaving(false);
  }, [book]);

  const discard = useCallback(async () => {
    setSaving(true);
    setSaveError(null);
    const result = await book.discard();
    if (!result.ok) setSaveError(result.error ?? "Could not discard.");
    setSaving(false);
  }, [book]);

  /**
   * "Keep as scenario" (SPEC §6-S2), composed host-side with **no new model
   * call**: one proposal carrying the fork plus the operation equivalent to the
   * hypothetical the answer was computed on, dry-run on the fork and confirmed
   * through the ordinary card.
   */
  const keepAsScenario = useCallback(
    async (turnId: string) => {
      const entry = conversation.entries.find(
        (e) => e.kind === "turn" && e.response.turn_id === turnId,
      );
      if (!entry || entry.kind !== "turn") return;
      const receipt = entry.response.receipts.find((r) => r.op === "project_balance");
      const request = (receipt?.request ?? {}) as Record<string, unknown>;
      const delta = request["delta"];
      if (typeof delta !== "string") return;
      const name = `whatif-${turnId.slice(0, 8)}`;
      const deltaDate = typeof request["delta_date"] === "string" ? request["delta_date"] : undefined;

      const { data, error, response } = await api.POST("/book/edits", {
        body: {
          origin: "button",
          ops: [
            { op: "fork_scenario", name, note: "kept from a what-if answer" },
            {
              op: "add_event",
              scenario: name,
              amount: delta,
              ...(deltaDate ? { date: deltaDate } : {}),
              note: "kept from a what-if answer",
            },
          ],
        },
      });
      if (error || !data) {
        setSaveError(describeError(error, response.status));
        return;
      }
      await book.refresh();
    },
    [conversation.entries, book],
  );

  const balance = useMemo(() => (state ? headlineBalance(state) : null), [state]);

  if (book.loading && !state) return <LoadingState label="Opening your book…" />;
  if (book.error) return <ErrorState message={book.error} onRetry={() => void book.refresh()} />;
  if (!state) {
    return (
      <View style={styles.screen}>
        <EmptyState
          title="You have no book yet."
          example="I earn 3,000 a month and pay 900 rent"
          testID={`${testID}-no-book`}
        />
      </View>
    );
  }

  const lowLabel = state.warnings.min_cash
    ? `LOW ${formatMoney(state.warnings.min_cash)}${
        state.warnings.min_cash_period ? ` · ${monthLabel(state.warnings.min_cash_period)}` : ""
      }`
    : "";

  return (
    <View testID={testID} style={styles.screen}>
      <View style={styles.header}>
        <Eyebrow testID={`${testID}-eyebrow`}>
          {`${state.book.id.toUpperCase()} · ${state.scenario.toUpperCase()} · AS-OF ${shortDate(state.as_of)}`}
        </Eyebrow>

        {/* SPEC §2.4: the header keeps showing base committed figures even
            while a fork is active. Naming the working context is not the same
            as taking a figure from it, so the two are separate elements — the
            eyebrow above stamps the figures, this line stamps the context. */}
        {book.activeScenario !== "base" ? (
          <Stamp tone="pine" testID={`${testID}-working-in`}>
            {`WORKING IN ${book.activeScenario.toUpperCase()} · FIGURES ABOVE ARE BASE`}
          </Stamp>
        ) : null}

        <WarningsBanner warnings={state.warnings} />

        <View style={styles.balanceRow}>
          <Text testID={`${testID}-balance`} style={[styles.balance, { color: color[moneyTone(balance?.value)] }]}>
            {formatMoney(balance?.value)}
          </Text>
          <Text style={styles.balanceLabel}>{balance?.label}</Text>
        </View>

        <Sparkline
          closing={state.closing}
          rangeLabel={`${state.months.length} MONTHS`}
          lowLabel={lowLabel}
          testID={`${testID}-sparkline`}
        />

        {/* The header's own figures are committed base state, so their stamp is
            the absence of one. This element still renders the payload's
            `what_if` faithfully — if the service ever stamps this payload, the
            screen says so rather than hiding it. */}
        <WhatIfStamp whatIf={state.what_if} testID={`${testID}-header-what-if`} />

        {state.dirty ? (
          <View testID={`${testID}-dirty`} style={styles.dirtyRow}>
            <Stamp tone="rust" testID={`${testID}-dirty-flag`}>
              UNSAVED CHANGES
            </Stamp>
            <View style={styles.dirtyActions}>
              <Button label="Discard" testID={`${testID}-discard`} disabled={saving} onPress={() => void discard()} />
              <Button
                label={saving ? "Saving…" : "Save"}
                variant="primary"
                testID={`${testID}-save`}
                disabled={saving}
                onPress={() => void save()}
              />
            </View>
          </View>
        ) : (
          <Stamp testID={`${testID}-clean`}>SAVED · REV {state.revision ? state.revision.slice(0, 7) : "—"}</Stamp>
        )}
        {saveError ? (
          <Text testID={`${testID}-save-error`} style={styles.saveError}>
            {saveError}
          </Text>
        ) : null}

        <Divider />
      </View>

      <ScrollView
        testID={`${testID}-stack`}
        style={styles.stack}
        contentContainerStyle={styles.stackContent}
      >
        {conversation.entries.length === 0 ? (
          <EmptyState
            title="Ask about your money, or say what changed."
            example="can I afford a 1,500 laptop in September?"
            testID={`${testID}-empty`}
          />
        ) : null}

        {conversation.entries.map((entry) => {
          if (entry.kind === "quote") {
            return (
              <QuoteRow key={entry.id} testID={`quote-${entry.id}`}>
                {entry.text}
              </QuoteRow>
            );
          }
          if (entry.kind === "failure") {
            return <ErrorState key={entry.id} message={entry.message} testID={`failure-${entry.id}`} />;
          }
          if (entry.kind === "resolution") {
            const resolution = entry.response;
            return (
              <View key={entry.id} style={styles.resolution}>
                <Stamp
                  testID={`resolution-${entry.id}`}
                  tone={resolution.kind === "applied" ? "pine" : resolution.kind === "refreshed" ? "rust" : "faint"}
                >
                  {resolution.kind === "applied"
                    ? `APPLIED · REV ${resolution.revision ? resolution.revision.slice(0, 7) : "UNCOMMITTED"}`
                    : resolution.kind === "discarded"
                      ? "DISCARDED"
                      : "THE BOOK MOVED · CONFIRM THE UPDATED CARD"}
                </Stamp>
                {resolution.kind === "refreshed" ? (
                  <ProposalCard
                    proposal={resolution.proposal}
                    busy={conversation.busy}
                    testID={`proposal-card-${entry.id}`}
                    onApply={(id) => void conversation.resolve(id, "accept")}
                    onDiscard={(id) => void conversation.resolve(id, "discard")}
                    onEdit={(prefill) => setDraft(prefill)}
                  />
                ) : null}
              </View>
            );
          }

          const turn = entry.response;
          if (turn.kind === "proposal" && turn.proposal) {
            return (
              <ProposalCard
                key={entry.id}
                proposal={turn.proposal}
                busy={conversation.busy}
                testID={`proposal-card-${entry.id}`}
                onApply={(id) => void conversation.resolve(id, "accept")}
                onDiscard={(id) => void conversation.resolve(id, "discard")}
                onEdit={(prefill) => setDraft(prefill)}
              />
            );
          }
          const tracePeriod = state.warnings.min_cash_period ?? state.months[0] ?? state.as_of;
          return (
            <AnswerCard
              key={entry.id}
              turn={turn}
              testID={`answer-card-${entry.id}`}
              busy={conversation.busy}
              canKeepAsScenario={turn.what_if?.stamped === true && turn.kind === "answer"}
              onKeepAsScenario={() => void keepAsScenario(turn.turn_id)}
              onDiscard={() => conversation.clear()}
              onTrace={() => onOpenTrace(tracePeriod, state.scenario)}
            />
          );
        })}
      </ScrollView>

      <View style={styles.footer}>
        <View style={styles.footerLinks}>
          <AsOfLine asOf={state.as_of} scenario={state.scenario} testID={`${testID}-as-of`} />
          <View style={styles.navRow}>
            {(
              [
                ["forecast", "FORECAST", onOpenForecast],
                ["scenarios", "SCENARIOS", onOpenScenarios],
                ["actuals", "ACTUALS", onOpenActuals],
                ["plan", "PLAN VS ACTUAL", onOpenPlan],
                ["import", "IMPORT / EXPORT", onOpenImport],
                ["settings", "SETTINGS", onOpenSettings],
              ] as const
            ).map(([key, label, go]) =>
              go ? (
                <Text key={key} testID={`${testID}-${key}-link`} style={styles.link} onPress={go}>
                  {`${label} ›`}
                </Text>
              ) : null,
            )}
          </View>
        </View>
        <AskBar
          value={draft}
          onChangeValue={setDraft}
          disabled={conversation.busy}
          onSubmit={(text) => void conversation.ask(text)}
          testID={`${testID}-ask`}
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
  },
  header: { gap: 10 },
  balanceRow: { flexDirection: "row", alignItems: "flex-end", gap: 10 },
  balance: { fontFamily: font.display, fontSize: 44, fontWeight: "500", letterSpacing: -1 },
  balanceLabel: { fontFamily: font.ui, fontSize: 13, color: color.sub, paddingBottom: 8 },
  dirtyRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", width: "100%" },
  dirtyActions: { flexDirection: "row", gap: 10, width: 200 },
  saveError: { fontFamily: font.ui, fontSize: 12, color: color.rust },
  stack: { flex: 1, marginTop: 14 },
  stackContent: { gap: 14, paddingBottom: 18 },
  resolution: { gap: 10, width: "100%" },
  footer: { gap: 10 },
  footerLinks: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 10 },
  navRow: { flexDirection: "row", flexWrap: "wrap", justifyContent: "flex-end", gap: 10, flexShrink: 1 },
  link: { fontFamily: font.mono, fontSize: 9.5, letterSpacing: 0.6, color: color.pine },
});
