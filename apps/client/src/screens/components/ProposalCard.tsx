/**
 * The confirmation card (SPEC §6-S4, §5-F2, ADR-0029).
 *
 * Every write in this product passes through this component. It shows what
 * will change, what the dry-run says the change does, and any diagnostics the
 * engine raised — then offers **Apply / Edit / Discard**, the one action
 * vocabulary the card, the API and the tests all use.
 *
 * Two properties this component must not quietly lose:
 *
 *  * **It never applies anything.** Apply posts `accept` and the screen renders
 *    what came back. If the service hands back a refreshed card instead of
 *    applied state, that is a *different card* and it is presented again.
 *  * **Every figure in the deltas block is the service's.** `before`, `after`
 *    and `change` all arrive computed; the card never subtracts one from
 *    another to show a movement.
 */
import React from "react";
import { Text, View, StyleSheet } from "react-native";

import type { Crossing, Deltas, MoneyMove, PeriodMove, Proposal } from "@cashkit/api-types";

import { formatMoney, moneyTone } from "../../money/money";
import { Button, Card, Divider, LeaderRow, Stamp } from "../../ui/atoms";
import { DiagnosticList, monthLabel, shortDate } from "../../ui/provenance";
import { color, font } from "../../ui/tokens";
import { describeOperation, proposalLabel } from "./describeOp";

function Move({ label, move, testID }: { label: string; move: MoneyMove; testID: string }) {
  return (
    <LeaderRow
      testID={testID}
      label={label}
      value={
        <Text>
          <Text style={{ color: color.sub }}>{formatMoney(move.before)}</Text>
          <Text style={{ color: color.faint }}>{"  →  "}</Text>
          <Text style={{ color: color[moneyTone(move.after)] }}>{formatMoney(move.after)}</Text>
        </Text>
      }
    />
  );
}

function PeriodRow({ label, move, testID }: { label: string; move: PeriodMove; testID: string }) {
  return (
    <LeaderRow
      testID={testID}
      label={label}
      tone="sub"
      value={
        <Text style={styles.periodValue}>
          {shortDate(move.before)} → {shortDate(move.after)}
        </Text>
      }
    />
  );
}

function Crossings({ crossings }: { crossings: readonly Crossing[] }) {
  if (crossings.length === 0) return null;
  return (
    <View testID="crossings" style={styles.crossings}>
      <Stamp tone="rust">
        {crossings.length === 1 ? "TURNS NEGATIVE IN 1 MONTH" : `TURNS NEGATIVE IN ${crossings.length} MONTHS`}
      </Stamp>
      {crossings.map((crossing) => (
        <LeaderRow
          key={crossing.period}
          testID={`crossing-${crossing.period}`}
          label={monthLabel(crossing.period)}
          tone="rust"
          value={
            <Text>
              <Text style={{ color: color.sub }}>{formatMoney(crossing.before)}</Text>
              <Text style={{ color: color.faint }}>{"  →  "}</Text>
              <Text style={{ color: color.rust }}>{formatMoney(crossing.after)}</Text>
            </Text>
          }
        />
      ))}
    </View>
  );
}

function DeltasBlock({ deltas }: { deltas: Deltas }) {
  return (
    <View testID="deltas" style={styles.deltas}>
      <Stamp>IF YOU APPLY THIS</Stamp>
      <Move label="Closing balance" move={deltas.closing_balance} testID="delta-closing" />
      <Move label="Lowest cash" move={deltas.min_cash} testID="delta-min-cash" />
      <PeriodRow label="Lowest cash falls in" move={deltas.min_cash_period} testID="delta-min-cash-period" />
      <PeriodRow label="Runway ends" move={deltas.runway_end} testID="delta-runway" />
      <Crossings crossings={deltas.crossings} />
      {deltas.affected_items.length > 0 ? (
        <Stamp>AFFECTS {deltas.affected_items.map((i) => `item:${i}`).join(" · ")}</Stamp>
      ) : null}
      {deltas.affected_events.length > 0 ? (
        <Stamp>AFFECTS {deltas.affected_events.map((e) => `event:${e}`).join(" · ")}</Stamp>
      ) : null}
    </View>
  );
}

export function ProposalCard({
  proposal,
  busy = false,
  onApply,
  onEdit,
  onDiscard,
  testID = "proposal-card",
}: {
  proposal: Proposal;
  busy?: boolean;
  onApply?: (id: string) => void;
  onEdit?: (prefill: string) => void;
  onDiscard?: (id: string) => void;
  testID?: string;
}) {
  const lines = proposal.operations.map((operation) => describeOperation(operation as Record<string, unknown>));
  const live = proposal.status === "pending";
  const blocking = proposal.diagnostics.some((d) => d.severity === "error");

  return (
    <Card tone="pending" testID={testID}>
      <View style={styles.labelRow}>
        <Stamp tone="sub" testID={`${testID}-label`}>
          {proposalLabel(proposal.operations as Record<string, unknown>[])}
        </Stamp>
        {!live ? (
          <Stamp tone={proposal.status === "accepted" ? "pine" : "faint"} testID={`${testID}-status`}>
            {proposal.status.toUpperCase()}
          </Stamp>
        ) : null}
      </View>

      {lines.map((line, index) => (
        <LeaderRow
          key={`${line.op}-${index}`}
          testID={`${testID}-op-${index}`}
          label={line.title}
          meta={line.meta || undefined}
          emphasis
          value={line.amount ?? "—"}
        />
      ))}

      <Divider />
      <DeltasBlock deltas={proposal.deltas} />

      {proposal.diagnostics.length > 0 ? (
        <>
          <Divider />
          <DiagnosticList diagnostics={proposal.diagnostics} testID={`${testID}-diagnostics`} />
        </>
      ) : null}

      {live ? (
        <View style={styles.actions}>
          <Button
            label="Discard"
            testID={`${testID}-discard`}
            disabled={busy}
            onPress={() => onDiscard?.(proposal.id)}
          />
          <Button
            label="Edit"
            testID={`${testID}-edit`}
            disabled={busy}
            onPress={() => onEdit?.(`Change that: `)}
          />
          <Button
            label="Apply"
            variant="primary"
            testID={`${testID}-apply`}
            disabled={busy || blocking}
            onPress={() => onApply?.(proposal.id)}
          />
        </View>
      ) : null}

      <View style={styles.footer}>
        <Stamp testID={`${testID}-provenance`}>
          {`${proposal.origin.toUpperCase()} · SCENARIO ${proposal.scenario.toUpperCase()}`}
          {proposal.base_revision ? ` · REV ${proposal.base_revision.slice(0, 7)}` : ""}
        </Stamp>
        {live ? (
          <Stamp testID={`${testID}-expiry`}>EXPIRES {proposal.expires_at.slice(11, 16)} UTC</Stamp>
        ) : null}
      </View>
      {blocking ? (
        <Text testID={`${testID}-blocked`} style={styles.blocked}>
          This cannot be applied while the engine is reporting an error above.
        </Text>
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  labelRow: { flexDirection: "row", justifyContent: "space-between", width: "100%" },
  deltas: { gap: 8, width: "100%" },
  crossings: { gap: 6, width: "100%", marginTop: 4 },
  actions: { flexDirection: "row", gap: 10, width: "100%" },
  footer: { flexDirection: "row", justifyContent: "space-between", width: "100%" },
  periodValue: { fontFamily: font.mono, fontSize: 11, color: color.sub },
  blocked: { fontFamily: font.ui, fontSize: 12, color: color.rust },
});
