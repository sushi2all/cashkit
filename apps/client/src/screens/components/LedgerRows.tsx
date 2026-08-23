/**
 * The ledger rows of SPEC §6-S7, including the correction scar.
 *
 * ADR-0012 makes a correction append-only: the original row is tombstoned, not
 * deleted, and a new row carries `corrects=<original id>` with a mandatory
 * note. ADR-0013 states what the interface owes that: "afterwards both rows
 * shown — original struck, correction linked. Visually distinct from an
 * ordinary edit; a correction leaves a scar by design."
 *
 * So this renders **both** rows. The original stays on the screen with its own
 * amount, struck; the correction sits under it with the annotation the design
 * specifies — `↳ corrected <date> · was <amount> · note: <note>`. Neither the
 * original figure nor the note is ever hidden, shortened or paraphrased: the
 * audit trail is the point, and an audit trail you have to go looking for is
 * not one.
 *
 * The rows come from `GET /book/events?include_voided=true`. Without that
 * parameter the tombstoned original is not on the wire at all, and there is
 * nothing to strike.
 */
import React from "react";
import { Text, View, StyleSheet } from "react-native";

import type { LedgerEvent } from "@cashkit/api-types";

import { formatMoney, moneyTone } from "../../money/money";
import { LeaderRow, Stamp } from "../../ui/atoms";
import { shortDate } from "../../ui/provenance";
import { color, font } from "../../ui/tokens";

export interface LedgerRow {
  event: LedgerEvent;
  /** The correction that replaced this row, if it was corrected. */
  correctedBy: LedgerEvent | null;
  /** The row this one corrects, if it is a correction. */
  corrects: LedgerEvent | null;
}

/**
 * Pair each event with the correction that replaced it, and each correction
 * with what it replaced. Pure bookkeeping over ids — no figure is touched.
 */
export function linkCorrections(events: readonly LedgerEvent[]): LedgerRow[] {
  const byId = new Map(events.map((e) => [e.id, e]));
  const correctionOf = new Map<string, LedgerEvent>();
  for (const event of events) {
    if (event.corrects) correctionOf.set(event.corrects, event);
  }
  return events.map((event) => ({
    event,
    correctedBy: correctionOf.get(event.id) ?? null,
    corrects: event.corrects ? (byId.get(event.corrects) ?? null) : null,
  }));
}

/** `ledger · import bank`, `ledger · voice` — where the row came from. */
function sourceMeta(event: LedgerEvent): string {
  const parts = ["ledger", event.source ?? "entered by hand"];
  if (event.item) parts.push(`item:${event.item}`);
  parts.push(`event:${event.id}`);
  return parts.join(" · ");
}

export function LedgerRowList({
  rows,
  onCorrect,
  testID = "ledger",
}: {
  rows: readonly LedgerRow[];
  /** Offered on an actual only: an actual is not editable, it is correctable. */
  onCorrect?: (event: LedgerEvent) => void;
  testID?: string;
}) {
  return (
    <>
      {rows.map(({ event, correctedBy, corrects }) => {
        const superseded = correctedBy !== null;
        return (
          <View key={event.id} testID={`${testID}-row-${event.id}`} style={styles.row}>
            <LeaderRow
              testID={`${testID}-row-${event.id}-line`}
              label={
                <Text style={superseded ? styles.struck : undefined}>
                  {event.note || event.item || "entry"}
                  <Text style={styles.date}>{` · ${shortDate(event.date)}`}</Text>
                </Text>
              }
              meta={sourceMeta(event)}
              value={
                <Text
                  testID={`${testID}-row-${event.id}-amount`}
                  style={superseded ? styles.struckValue : undefined}
                >
                  {formatMoney(event.amount)}
                </Text>
              }
              tone={superseded ? "sub" : moneyTone(event.amount)}
              emphasis={!superseded}
              onPress={
                !superseded && event.status === "actual" && onCorrect
                  ? () => onCorrect(event)
                  : undefined
              }
            />

            {superseded ? (
              <Stamp tone="rust" testID={`${testID}-row-${event.id}-superseded`}>
                {`CORRECTED · SEE ${correctedBy.id}`}
              </Stamp>
            ) : null}

            {corrects ? (
              // The annotation SPEC §6-S7 specifies, with the original figure
              // and the mandatory note both on the screen (ADR-0012).
              <Text testID={`${testID}-row-${event.id}-correction`} style={styles.correction}>
                {`↳ corrected ${shortDate(event.date)} · was ${formatMoney(corrects.amount)} · note: ${
                  event.note ?? ""
                }`}
              </Text>
            ) : null}

            {!superseded && event.status === "actual" && onCorrect ? (
              <Text
                testID={`${testID}-row-${event.id}-correct`}
                accessibilityRole="button"
                onPress={() => onCorrect(event)}
                style={styles.correctLink}
              >
                RECORD A CORRECTION ›
              </Text>
            ) : null}
          </View>
        );
      })}
    </>
  );
}

const styles = StyleSheet.create({
  row: { width: "100%", gap: 3 },
  struck: { textDecorationLine: "line-through", color: color.faint },
  struckValue: { textDecorationLine: "line-through", color: color.faint },
  date: { fontFamily: font.ui, fontSize: 12, color: color.faint },
  correction: { fontFamily: font.mono, fontSize: 9, color: color.rust, letterSpacing: 0.3 },
  correctLink: { fontFamily: font.mono, fontSize: 8.5, letterSpacing: 0.6, color: color.pine },
});
