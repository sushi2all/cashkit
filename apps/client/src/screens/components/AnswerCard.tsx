/**
 * The answer card — a receipt, not a chat bubble (ADR-0023).
 *
 * It renders three of the four turn kinds; `proposal` has its own card.
 *
 *  * **`answer`** — the verdict sentence, then one leader-dot row per figure
 *    the engine produced. `receipts[]` **may be empty** and the reply is still
 *    the answer (S2 handoff §5): a read turn can be answered straight from the
 *    state snapshot's results block. The card renders that case rather than
 *    treating it as a broken response.
 *  * **`clarification`** — the model's question. Nothing changed, and the card
 *    says so, because a question that looks like a result is a trap.
 *  * **`refusal`** — a SPEC §8 guardrail. It arrives on a **200** with
 *    `retry_after_seconds` and reads as a sentence (D-MLP-24). It is not an
 *    error toast and it is not a failure state.
 *
 * `diagnostics[]` on the turn is separate from `proposal.diagnostics`: this is
 * what the host refused or deferred — a host operation the model reached for,
 * a `save` it asked for. Both render verbatim (ADR-0015).
 */
import React from "react";
import { Text, View, StyleSheet } from "react-native";

import type { TurnResponse } from "@cashkit/api-types";

import { formatMoney, moneyTone } from "../../money/money";
import { Button, Card, Divider, LeaderRow, Stamp, Verdict } from "../../ui/atoms";
import { DiagnosticList, WhatIfStamp } from "../../ui/provenance";
import { color, font } from "../../ui/tokens";
import { receiptFigures, receiptNotes } from "./receiptFigures";

/** The engine diagnostic code a negative what-if answer stamps on its footer. */
function firstErrorCode(turn: TurnResponse): string | null {
  const diagnostic = turn.diagnostics.find((d) => d.severity === "error" || d.severity === "warning");
  return diagnostic ? diagnostic.code : null;
}

export function AnswerCard({
  turn,
  onTrace,
  onDiscard,
  onKeepAsScenario,
  canKeepAsScenario = false,
  busy = false,
  testID = "answer-card",
}: {
  turn: TurnResponse;
  onTrace?: () => void;
  /** SPEC §6-S2: the negative what-if variant swaps the actions. */
  onDiscard?: () => void;
  onKeepAsScenario?: () => void;
  canKeepAsScenario?: boolean;
  busy?: boolean;
  testID?: string;
}) {
  const figures = turn.receipts.flatMap((receipt) => receiptFigures(receipt));
  const notes = turn.receipts.flatMap((receipt) => receiptNotes(receipt));
  const negative = figures.some((figure) => moneyTone(figure.value) === "rust");

  return (
    <Card testID={testID}>
      <Verdict testID={`${testID}-reply`}>{turn.reply}</Verdict>

      {turn.kind === "clarification" ? (
        <Stamp testID={`${testID}-clarification`}>NOTHING WAS CHANGED</Stamp>
      ) : null}

      {turn.kind === "refusal" ? (
        <Stamp testID={`${testID}-refusal`} tone="rust">
          {typeof turn.retry_after_seconds === "number"
            ? `TRY AGAIN IN ${turn.retry_after_seconds}s`
            : "TRY AGAIN LATER"}
        </Stamp>
      ) : null}

      {figures.map((figure, index) => (
        <LeaderRow
          key={`${figure.label}-${index}`}
          testID={`${testID}-figure-${index}`}
          label={figure.label}
          tone={moneyTone(figure.value)}
          value={formatMoney(figure.value)}
        />
      ))}

      {notes.map((note, index) => (
        <Text key={`note-${index}`} testID={`${testID}-note-${index}`} style={styles.note}>
          {note}
        </Text>
      ))}

      {turn.kind === "answer" && figures.length === 0 ? (
        // A read turn answered from the state snapshot carries no receipt. The
        // sentence above is the answer; saying so beats an empty card.
        <Stamp testID={`${testID}-no-receipts`}>ANSWERED FROM THE BOOK · NO SEPARATE RECEIPT</Stamp>
      ) : null}

      {turn.diagnostics.length > 0 ? (
        <>
          <Divider />
          <DiagnosticList diagnostics={turn.diagnostics} testID={`${testID}-diagnostics`} />
        </>
      ) : null}

      {canKeepAsScenario && negative ? (
        <View style={styles.actions}>
          <Button label="Discard" testID={`${testID}-discard`} disabled={busy} onPress={() => onDiscard?.()} />
          <Button
            label="Keep as scenario"
            variant="primary"
            testID={`${testID}-keep`}
            disabled={busy}
            onPress={() => onKeepAsScenario?.()}
          />
        </View>
      ) : null}

      <Divider />
      <View style={styles.footer}>
        <WhatIfStamp
          whatIf={turn.what_if}
          diagnosticCode={firstErrorCode(turn)}
          note={negative && turn.what_if?.stamped ? "negative cash" : undefined}
          testID={`${testID}-what-if`}
        />
        {onTrace ? (
          <Text testID={`${testID}-trace-link`} style={styles.traceLink} onPress={onTrace}>
            TRACE ›
          </Text>
        ) : null}
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  note: { fontFamily: font.ui, fontSize: 12, color: color.sub },
  actions: { flexDirection: "row", gap: 10, width: "100%" },
  footer: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", width: "100%" },
  traceLink: { fontFamily: font.mono, fontSize: 10, letterSpacing: 0.7, color: color.pine },
});
