/**
 * Provenance elements — the parts of the UI that say where a number came from.
 *
 * SPEC §6 is explicit that these are *elements, not decoration*: every screen
 * carrying a computed figure shows the as-of stamp and the active scenario,
 * and every hypothetical figure carries the WHAT-IF stamp. The rule they
 * implement is SPEC §2.4, quoted here verbatim because the PROMPT requires its
 * wording wherever it is restated:
 *
 * > Base is the plan of record. Any figure NOT from the committed state of
 * > `base` — a non-base scenario (active or not), a throwaway overlay, or a
 * > dry-run including pending changes — carries the WHAT-IF stamp: payload
 * > field `what_if: {stamped: true, reason: "scenario"|"overlay"|"pending",
 * > scenario?: id}`, and a rendered stamp element (ADR-0024). The Home header
 * > and sparkline always show base committed figures, in neutral form, even
 * > while a fork is active; a fork's own figures render stamped with the
 * > fork's name.
 */
import React from "react";
import { Text, View, StyleSheet } from "react-native";

import type { Diagnostic, WhatIf } from "@cashkit/api-types";

import { Stamp } from "./atoms";
import { color, font } from "./tokens";

/** Format a service date (`2026-10-27`) as the receipt language sets them. */
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parts = iso.slice(0, 10).split("-");
  const [year, month, day] = parts;
  if (!year || !month || !day) return iso;
  const name = MONTHS[monthIndex(month)] ?? month;
  return `${day.replace(/^0/, "")} ${name} ${year}`;
}

export function monthLabel(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parts = iso.slice(0, 10).split("-");
  const [year, month] = parts;
  if (!year || !month) return iso;
  const name = MONTHS[monthIndex(month)] ?? month;
  return `${name} ${year}`;
}

/** Month number to index, by table lookup — the client parses no numbers. */
function monthIndex(month: string): number {
  return ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"].indexOf(month);
}

/**
 * The ADR-0024 stamp. Rendered whenever `what_if.stamped` is true and never
 * otherwise: a stamp on a committed figure would be as wrong as a missing one.
 */
export function WhatIfStamp({
  whatIf,
  diagnosticCode,
  note,
  testID = "what-if-stamp",
}: {
  whatIf: WhatIf | null | undefined;
  diagnosticCode?: string | null;
  note?: string;
  testID?: string;
}) {
  if (!whatIf?.stamped) return null;
  const parts = ["WHAT-IF"];
  if (whatIf.reason === "pending") parts.push("INCLUDES PENDING");
  if (whatIf.reason === "overlay") parts.push("OVERLAY");
  if (whatIf.reason === "scenario") parts.push(`SCENARIO ${(whatIf.scenario ?? "").toUpperCase()}`);
  if (diagnosticCode) parts.push(diagnosticCode);
  if (note) parts.push(note.toUpperCase());
  return (
    <Stamp testID={testID} tone={diagnosticCode ? "rust" : "faint"}>
      {parts.join(" · ")}
    </Stamp>
  );
}

/** The as-of / scenario subline every screen with computed figures carries. */
export function AsOfLine({
  asOf,
  scenario,
  prefix,
  testID = "as-of-stamp",
}: {
  asOf: string;
  scenario: string;
  prefix?: string;
  testID?: string;
}) {
  const parts = [];
  if (prefix) parts.push(prefix.toUpperCase());
  parts.push(`SCENARIO ${scenario.toUpperCase()}`);
  parts.push(`AS-OF ${shortDate(asOf)}`);
  return (
    <Text testID={testID} style={styles.subline}>
      {parts.join(" · ")}
    </Text>
  );
}

/**
 * The engine panel of SPEC §6-S5: engine version, rounding order, revision.
 * This is where `exact` belongs — the one place full precision is the point.
 */
export function EnginePanel({
  engineVersion,
  revision,
  testID = "engine-panel",
}: {
  engineVersion: string;
  revision: string | null;
  testID?: string;
}) {
  const rows: [string, string][] = [
    ["ENGINE", `v${engineVersion} · DETERMINISTIC`],
    ["ROUNDING", "CANONICAL ORDER · 4DP"],
    ["BOOK REVISION", revision ? revision.slice(0, 7) : "UNCOMMITTED"],
  ];
  return (
    <View testID={testID} style={styles.engine}>
      {rows.map(([k, v]) => (
        <View key={k} style={styles.engineRow}>
          <Text style={styles.engineKey}>{k}</Text>
          <Text testID={`engine-${k.toLowerCase().replace(/ /g, "-")}`} style={styles.engineValue}>
            {v}
          </Text>
        </View>
      ))}
    </View>
  );
}

/**
 * Diagnostics, verbatim.
 *
 * ADR-0015 and PROMPT non-negotiable 5: never rewritten, never summarized,
 * never suppressed, never turned into advice. Every field the service sent is
 * on the screen — code, severity, message, suggested fix — and the component
 * has no branch that shortens or reinterprets any of them.
 */
export function DiagnosticList({
  diagnostics,
  testID = "diagnostics",
}: {
  diagnostics: readonly Diagnostic[] | null | undefined;
  testID?: string;
}) {
  if (!diagnostics || diagnostics.length === 0) return null;
  return (
    <View testID={testID} style={styles.diagnostics}>
      {diagnostics.map((d, index) => (
        <View key={`${d.code}-${index}`} testID={`${testID}-item`} style={styles.diagnostic}>
          <Text style={[styles.diagnosticCode, severityTone(d.severity)]}>
            {d.code} · {d.severity.toUpperCase()}
            {d.item_id ? ` · ${d.item_id}` : ""}
            {d.field ? ` · ${d.field}` : ""}
          </Text>
          <Text style={styles.diagnosticMessage}>{d.message}</Text>
          {d.suggested_fix ? <Text style={styles.diagnosticFix}>{d.suggested_fix}</Text> : null}
        </View>
      ))}
    </View>
  );
}

function severityTone(severity: string) {
  if (severity === "error") return { color: color.rust };
  if (severity === "warning") return { color: color.rust };
  return { color: color.sub };
}

const styles = StyleSheet.create({
  subline: { fontFamily: font.mono, fontSize: 9.5, letterSpacing: 0.7, color: color.sub },
  engine: {
    borderWidth: 1,
    borderColor: color.hair,
    borderRadius: 8,
    paddingVertical: 14,
    paddingHorizontal: 16,
    gap: 6,
    width: "100%",
  },
  engineRow: { flexDirection: "row", justifyContent: "space-between", width: "100%" },
  engineKey: { fontFamily: font.mono, fontSize: 9.5, color: color.sub },
  engineValue: { fontFamily: font.mono, fontSize: 9.5, color: color.ink },
  diagnostics: { gap: 10, width: "100%" },
  diagnostic: { gap: 3 },
  diagnosticCode: { fontFamily: font.mono, fontSize: 9, letterSpacing: 0.7 },
  diagnosticMessage: { fontFamily: font.ui, fontSize: 13, color: color.ink },
  diagnosticFix: { fontFamily: font.ui, fontSize: 12, color: color.sub, fontStyle: "italic" },
});
