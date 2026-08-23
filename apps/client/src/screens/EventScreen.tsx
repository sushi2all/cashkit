/**
 * Screen 10 — Item, one-off (SPEC §6-S10).
 *
 * The ledger rows for one item, or one event on its own. Everything the design
 * asks for is on the wire here: amount, date, direction, note, status, the
 * creation channel (`source`), and the event id.
 *
 * **Remove is offered on a forecast and refused on an actual**, and the screen
 * does not hide that — it says why. An actual is a fact; removing the record of
 * a fact destroys it, and correcting it is M6, which leaves a scar (ADR-0012).
 * The applier refuses `remove_event` on an actual rather than choosing for the
 * user (SPEC §2.5), so the interface points at the correction instead.
 *
 * Both actions are proposals through `POST /book/edits`. Nothing here writes.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ScrollView, Text, View, StyleSheet } from "react-native";

import type { LedgerEvent, WhatIf } from "@cashkit/api-types";

import { api, describeError } from "../api/client";
import { formatMoney, moneyTone } from "../money/money";
import { useBook } from "../state/book";
import { useEditProposal } from "../state/edits";
import { Button, Card, LeaderRow, Stamp } from "../ui/atoms";
import { AsOfLine, monthLabel, shortDate, WhatIfStamp } from "../ui/provenance";
import { EmptyState, ErrorState, LoadingState } from "../ui/states";
import { color, font, space } from "../ui/tokens";
import { LedgerRowList, linkCorrections } from "./components/LedgerRows";
import { ProposalCard } from "./components/ProposalCard";

export function EventScreen({
  itemId,
  eventId,
  scenario,
  onBack,
  onOpenActuals,
  testID = "event-screen",
}: {
  /** Show every ledger row carrying this item. */
  itemId?: string;
  /** Or one event by id. */
  eventId?: string;
  scenario?: string;
  onBack: () => void;
  onOpenActuals?: () => void;
  testID?: string;
}) {
  const book = useBook();
  const [events, setEvents] = useState<LedgerEvent[] | null>(null);
  const [asOf, setAsOf] = useState<string>("");
  const [revision, setRevision] = useState<string | null>(null);
  const [resolvedScenario, setResolvedScenario] = useState("base");
  const [whatIf, setWhatIf] = useState<WhatIf | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const query = scenario ? { scenario } : {};
    const response = await api.GET("/book/events", {
      params: { query: { ...query, include_voided: true } },
    });
    setLoading(false);
    if (response.error || !response.data) {
      setError(describeError(response.error, response.response.status));
      return;
    }
    setError(null);
    setEvents(response.data.events);
    setAsOf(response.data.as_of);
    setRevision(response.data.revision);
    setResolvedScenario(response.data.scenario);
    setWhatIf(response.data.what_if);
  }, [scenario]);

  useEffect(() => {
    void load();
  }, [load]);

  const edit = useEditProposal({
    onApplied: async () => {
      await book.refresh();
      await load();
    },
  });

  const remove = useCallback(
    async (event: LedgerEvent) => {
      await edit.propose(
        [{ op: "remove_event", event: event.id, note: "removed from the plan" }],
        { origin: "cell_edit", ...(scenario ? { scenario } : {}) },
      );
    },
    [edit, scenario],
  );

  const rows = useMemo(() => {
    const all = events ?? [];
    const picked = eventId
      ? all.filter((e) => e.id === eventId || e.corrects === eventId)
      : itemId
        ? all.filter((e) => e.item === itemId)
        : all;
    return linkCorrections(picked);
  }, [events, eventId, itemId]);

  if (loading && !events) return <LoadingState label="Reading the ledger…" />;
  if (error) return <ErrorState message={error} onRetry={() => void load()} testID={`${testID}-error`} />;

  const single = rows.length === 1 ? rows[0]!.event : null;

  return (
    <View testID={testID} style={styles.screen}>
      <Text testID={`${testID}-back`} style={styles.crumb} onPress={onBack}>
        ‹ EVENT
      </Text>

      <Text testID={`${testID}-title`} style={styles.title}>
        {single ? single.note || single.item || "One-off" : (itemId ?? "Ledger")}
      </Text>
      <Text testID={`${testID}-subline`} style={styles.subline}>
        {`${single ? `evt:${single.id}` : `item:${itemId ?? "—"}`} · SCENARIO ${resolvedScenario.toUpperCase()} · AS-OF ${shortDate(
          asOf,
        )}`}
      </Text>
      <WhatIfStamp whatIf={whatIf} testID={`${testID}-what-if`} />

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {rows.length === 0 ? (
          <EmptyState
            title="No one-off has been recorded here."
            example="I paid 1,800 for the October trip"
            testID={`${testID}-empty`}
          />
        ) : null}

        {single ? (
          <Card testID={`${testID}-event-card`}>
            <Stamp tone="sub">EVENT</Stamp>
            <LeaderRow
              testID={`${testID}-amount`}
              label="Amount"
              value={formatMoney(single.amount)}
              tone={moneyTone(single.amount)}
              emphasis
            />
            <LeaderRow
              testID={`${testID}-date`}
              label="Date"
              value={<Text style={styles.engineWords}>{shortDate(single.date)}</Text>}
              tone="sub"
            />
            <LeaderRow
              testID={`${testID}-direction`}
              label="Direction"
              value={
                <Text style={styles.engineWords}>
                  {formatMoney(single.amount).startsWith("−") ? "out" : "in"}
                </Text>
              }
              tone="sub"
            />
            <LeaderRow
              testID={`${testID}-note`}
              label="Note"
              value={<Text style={styles.engineWords}>{single.note ?? "—"}</Text>}
              tone="sub"
            />
          </Card>
        ) : null}

        <Card testID={`${testID}-rows-card`}>
          <Stamp tone="sub" testID={`${testID}-rows-label`}>
            {`LEDGER · ${monthLabel(asOf).toUpperCase()} AND EARLIER`}
          </Stamp>
          <LedgerRowList rows={rows} testID={`${testID}-ledger`} />
        </Card>

        {single ? (
          <Card testID={`${testID}-status-card`}>
            <Stamp tone="sub">{`STATUS: ${single.status.toUpperCase()}`}</Stamp>
            <Text testID={`${testID}-status-explainer`} style={styles.explainer}>
              {single.status === "actual"
                ? "This happened. The record of it can be corrected, and the correction leaves both rows on the ledger — it cannot be removed."
                : "Still a forecast. When you record the payment, the ledger takes over and this event stops counting."}
            </Text>
            {single.status === "actual" ? (
              <>
                <Stamp tone="rust" testID={`${testID}-remove-refused`}>
                  REMOVE IS REFUSED ON AN ACTUAL · CORRECTIONS ONLY
                </Stamp>
                {onOpenActuals ? (
                  <Button
                    label="Record a correction on the Actuals screen"
                    testID={`${testID}-go-correct`}
                    onPress={onOpenActuals}
                  />
                ) : null}
              </>
            ) : (
              <Button
                label="Remove this event"
                testID={`${testID}-remove`}
                disabled={edit.busy}
                onPress={() => void remove(single)}
              />
            )}
          </Card>
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

        <Card testID={`${testID}-provenance`}>
          <Stamp tone="sub">PROVENANCE</Stamp>
          {single ? (
            <>
              <LeaderRow
                label="Event"
                value={<Text style={styles.engineWords}>{`evt:${single.id}`}</Text>}
                tone="sub"
              />
              <LeaderRow
                label="Channel"
                value={<Text style={styles.engineWords}>{single.source ?? "entered by hand"}</Text>}
                tone="sub"
              />
              {single.ext_id ? (
                <LeaderRow
                  label="External id"
                  value={<Text style={styles.engineWords}>{single.ext_id}</Text>}
                  tone="sub"
                />
              ) : null}
            </>
          ) : null}
          <LeaderRow
            label="Book revision"
            value={
              <Text style={styles.engineWords}>{revision ? revision.slice(0, 7) : "UNCOMMITTED"}</Text>
            }
            tone="sub"
          />
        </Card>
      </ScrollView>

      <View style={styles.footer}>
        <AsOfLine asOf={asOf} scenario={resolvedScenario} testID={`${testID}-as-of`} />
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
  title: { fontFamily: font.display, fontSize: 28, fontWeight: "600", color: color.ink },
  subline: { fontFamily: font.mono, fontSize: 9.5, letterSpacing: 0.6, color: color.sub },
  body: { flex: 1 },
  bodyContent: { gap: 14, paddingBottom: 12 },
  engineWords: { fontFamily: font.mono, fontSize: 10, color: color.ink },
  explainer: { fontFamily: font.ui, fontSize: 13, color: color.sub },
  footer: { gap: 6 },
});
