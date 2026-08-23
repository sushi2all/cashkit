/**
 * Screen 14 — Import and export (SPEC §6-S14, §7).
 *
 * No `design.pen` reference; SPEC §6-S14's element inventory is the
 * specification, and the receipt vocabulary of ADR-0023 is the same here as
 * everywhere else: dotted-leader rows, monospace provenance, no chat bubbles.
 *
 * The screen has one job that is genuinely different from every other screen
 * in this app: it watches something slow happen and then asks a question about
 * it. Four rules shape it.
 *
 *  * **The report is the substance; the card is the change.** The
 *    reconciliation report is what tells the user whether their spreadsheet
 *    came back correctly, per sheet row — matched, mismatched with the delta,
 *    or skipped with the reason, and the 1-cent parity label where it applies
 *    (SPEC §7.5). The proposal card underneath is the same `ProposalCard`
 *    every other write uses, confirmed through the same `useEditProposal`
 *    path. Nothing here applies anything (ADR-0029).
 *  * **Where the file is going is said before it goes.** `POST /import`
 *    answers with the SPEC §7.3 target, decided before the first model call,
 *    so a book that already has a plan says so at the top of the run rather
 *    than in the report at the end.
 *  * **A mismatch is shown, never smoothed.** A divergence the engine and the
 *    spreadsheet disagree on is rendered with both figures and the delta. The
 *    parity note is a label on a mismatch, not a pass.
 *  * **Sheet figures are not engine figures, and the screen keeps them
 *    apart.** An engine figure is rendered through `formatMoney`, in euros,
 *    like every other figure in the app. A spreadsheet cell is rendered as the
 *    monospace string the workbook held. The distinction is the whole point of
 *    a reconciliation.
 *
 * Import is web-primary in the MLP: SPEC §6-S14 points mobile at the web app,
 * and the screen renders that pointer rather than a dead button.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ScrollView, Text, TextInput, View, StyleSheet } from "react-native";

import type { ImportCheck, ImportDone, ReconciliationReport } from "@cashkit/api-types";

import { DELIVERY, saveExport, type ExportRequest } from "../exporting/download";
import { CAN_PICK_FILE, mountFileInput, openFileInput } from "../importing/filePicker";
import { formatMoney, moneyTone } from "../money/money";
import { useBook } from "../state/book";
import { useEditProposal } from "../state/edits";
import { useImportJob, type ImportEvent } from "../state/importJob";
import { Button, Card, Divider, Eyebrow, LeaderRow, Stamp } from "../ui/atoms";
import { DiagnosticList, WhatIfStamp, shortDate } from "../ui/provenance";
import { ErrorState } from "../ui/states";
import { color, font, radius, space } from "../ui/tokens";
import { ProposalCard } from "./components/ProposalCard";

const FILE_INPUT_TESTID = "import-file-input";
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * The window choices, as numbers rather than as a text field.
 *
 * A typed month count would have to be parsed, and `Number`/`parseInt` are
 * banned outside the chart-geometry quarantine (D-MLP-50) — the rule is
 * deliberately blunt, because a narrow one leaves the laundering hole open.
 * Three choices need no conversion at all, and the export window is a choice
 * rather than free text anyway.
 */
const WINDOWS = [6, 12, 24] as const;

/** The one example ask SPEC §6 requires an empty state to carry. */
const EXAMPLE = "drop last year's budget.xlsx and see what the engine makes of it";

const STATUS_TONE: Record<ImportCheck["status"], "pine" | "rust" | "sub"> = {
  matched: "pine",
  mismatched: "rust",
  skipped: "sub",
};

/** A progress line the user reads. The service's own words where it has them. */
function describeEvent(event: ImportEvent): string | null {
  switch (event.stage) {
    case "parsing":
      return `Reading ${String(event.filename)}…`;
    case "parsed":
      return `Read ${String((event.sheets as string[] | undefined)?.join(", ") ?? "")} — ${String(event.cells)} filled cells.`;
    case "target":
      return String(event.message ?? "");
    case "planning":
      return "Working out the sections and which cells are the sheet's own totals…";
    case "planned":
      return `${String(event.reply ?? "")} ${String((event.sections as string[] | undefined)?.length ?? 0)} sections, ${String(event.checks)} checks.`;
    case "section":
      return `Section ${String(event.index)} of ${String(event.of)}: ${String(event.section)}`;
    case "authored":
      return `${String(event.section)} — ${String(event.operations)} lines. ${String(event.reply ?? "")}`;
    case "revising":
      return `Reconciling ${String(event.section)} again (round ${String(event.round)}): ${String(event.failing)} checks still disagree.`;
    case "dropped":
      return `A line was left out of ${String(event.section)}: the engine refused it.`;
    default:
      return null;
  }
}

function CheckRow({ check, testID }: { check: ImportCheck; testID: string }) {
  return (
    <View testID={testID} style={styles.check}>
      <LeaderRow
        testID={`${testID}-row`}
        label={check.label}
        meta={`${check.ref} · ${check.measure}${check.basis === "added" ? " · added by this import" : ""}`}
        tone={STATUS_TONE[check.status]}
        value={
          <Text testID={`${testID}-status`} style={styles.status}>
            {check.status.toUpperCase()}
            {check.parity ? " · 1-CENT PARITY" : ""}
          </Text>
        }
      />
      {check.status === "skipped" ? null : (
        <View style={styles.figures}>
          <Text testID={`${testID}-sheet`} style={styles.sheetFigure}>
            sheet {check.sheet_value ?? "—"}
          </Text>
          <Text style={styles.arrow}>·</Text>
          <Text
            testID={`${testID}-engine`}
            style={[styles.engineFigure, { color: color[moneyTone(check.engine_value)] }]}
          >
            engine {formatMoney(check.engine_value)}
          </Text>
          {check.status === "mismatched" ? (
            <Text testID={`${testID}-delta`} style={styles.deltaFigure}>
              Δ {check.delta ?? "—"}
            </Text>
          ) : null}
        </View>
      )}
      {check.note ? (
        <Text testID={`${testID}-note`} style={styles.note}>
          {check.note}
        </Text>
      ) : null}
    </View>
  );
}

function Report({ report, testID }: { report: ReconciliationReport; testID: string }) {
  const checks = report.checks ?? [];
  return (
    <Card testID={testID}>
      <Stamp tone="sub">RECONCILIATION · {report.source_filename.toUpperCase()}</Stamp>
      <Text testID={`${testID}-summary`} style={styles.summary}>
        {report.matched} matched · {report.mismatched} mismatched · {report.skipped} skipped
      </Text>
      <Stamp testID={`${testID}-target`}>
        {report.created_fork
          ? `INTO SCENARIO ${report.target_scenario.toUpperCase()} · BASE UNTOUCHED`
          : "INTO THE PLAN · THIS BOOK WAS EMPTY"}
      </Stamp>
      <Stamp testID={`${testID}-calls`}>
        {report.llm_calls} OF {report.call_cap} ASSISTANT CALLS
        {report.capped ? " · CAP REACHED" : ""}
      </Stamp>
      {report.incomplete_reason ? (
        <Text testID={`${testID}-incomplete`} style={styles.incomplete}>
          {report.incomplete_reason}
        </Text>
      ) : null}

      <Divider />
      {checks.length === 0 ? (
        <Text testID={`${testID}-nochecks`} style={styles.note}>
          This sheet has no total or balance rows of its own, so there was nothing to
          reconcile against. Read the lines on the card below before applying it.
        </Text>
      ) : (
        checks.map((check, index) => (
          <CheckRow key={`${check.ref}-${index}`} check={check} testID={`${testID}-check-${index}`} />
        ))
      )}

      {report.parity_notes > 0 ? (
        <Text testID={`${testID}-parity-note`} style={styles.note}>
          {report.parity_notes} row{report.parity_notes === 1 ? "" : "s"} differ by at most{" "}
          {report.parity_tolerance}. The engine works in fixed point at four decimals with
          banker&rsquo;s rounding and a spreadsheet uses floating point, so an exact tie lands one
          cent apart. These are reported, not corrected.
        </Text>
      ) : null}

      <Stamp testID={`${testID}-legend`}>
        SHEET FIGURES ARE THE WORKBOOK&rsquo;S OWN · ENGINE FIGURES ARE COMPUTED
      </Stamp>
    </Card>
  );
}

export function ImportExportScreen({
  onBack,
  testID = "import-screen",
}: {
  onBack: () => void;
  testID?: string;
}) {
  const book = useBook();
  const job = useImportJob();

  const [mode, setMode] = useState<"budget" | "ledger">("budget");
  const [months, setMonths] = useState<(typeof WINDOWS)[number]>(12);
  const [start, setStart] = useState("");
  const [exportNote, setExportNote] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const edit = useEditProposal({ onApplied: () => book.refresh() });

  const onFile = useCallback(
    (file: unknown) => {
      edit.reset();
      const name = (file as { name?: string }).name ?? "budget.xlsx";
      void job.start(file, name).then((done) => {
        if (done?.proposal) edit.adopt(done.proposal);
      });
    },
    [edit, job],
  );

  useEffect(() => mountFileInput(FILE_INPUT_TESTID, onFile), [onFile]);

  const download = useCallback(async () => {
    setExportNote(null);
    setExportError(null);
    const request: ExportRequest = {
      mode,
      months,
      ...(ISO_DATE.test(start.trim()) ? { start: start.trim() } : {}),
    };
    const result = await saveExport(request);
    if (result.ok) {
      setExportNote(
        DELIVERY === "download"
          ? `${result.filename} is in your downloads.`
          : `${result.filename} was handed to the share sheet.`,
      );
    } else {
      setExportError(result.error ?? "The export did not work.");
    }
  }, [mode, months, start]);

  const lines = useMemo(
    () =>
      job.events
        .map((event, index) => ({ index, text: describeEvent(event) }))
        .filter((line): line is { index: number; text: string } => Boolean(line.text)),
    [job.events],
  );
  const liveChecks = useMemo(
    () => job.events.filter((event) => event.stage === "check") as unknown as ImportCheck[],
    [job.events],
  );
  const done: ImportDone | null = job.done;
  const state = book.state;

  return (
    <View testID={testID} style={styles.screen}>
      <View style={styles.headerRow}>
        <Text testID={`${testID}-back`} style={styles.back} onPress={onBack}>
          ‹ BACK
        </Text>
        <Text style={styles.title}>Import &amp; export</Text>
      </View>
      <Eyebrow testID={`${testID}-eyebrow`}>
        {state
          ? `${state.book.id} · ${book.activeScenario.toUpperCase()} · AS-OF ${shortDate(state.as_of)}`
          : "NO BOOK"}
      </Eyebrow>

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        <Card testID={`${testID}-import-card`}>
          <Stamp tone="sub">IMPORT A SPREADSHEET</Stamp>
          {CAN_PICK_FILE ? (
            <>
              <Text style={styles.explainer}>
                Pick an .xlsx budget. The engine rebuilds it, checks its own figures against the
                sheet&rsquo;s own total and balance rows, and hands you one card to confirm. Nothing
                changes until you apply it.
              </Text>
              <Button
                label={job.busy ? "Importing…" : "Choose a workbook"}
                variant="primary"
                testID={`${testID}-choose`}
                disabled={job.busy}
                onPress={() => openFileInput(FILE_INPUT_TESTID)}
              />
              {!job.started && !job.busy && !done ? (
                <Text testID={`${testID}-empty-example`} style={styles.example}>
                  try: {EXAMPLE}
                </Text>
              ) : null}
            </>
          ) : (
            <Text testID={`${testID}-web-only`} style={styles.explainer}>
              Importing a spreadsheet happens in the web app. Open CashKit in a browser and drop
              the file there; the book is the same one.
            </Text>
          )}

          {job.refusal ? (
            <Text testID={`${testID}-refusal`} style={styles.refusal}>
              {job.refusal.reply}
            </Text>
          ) : null}

          {job.started ? (
            <>
              <Divider />
              <Stamp
                testID={`${testID}-target`}
                tone={job.started.target.created_fork ? "rust" : "sub"}
              >
                {job.started.target.created_fork
                  ? `NEW SCENARIO ${job.started.target.scenario.toUpperCase()}`
                  : "INTO THE PLAN"}
              </Stamp>
              <Text testID={`${testID}-target-message`} style={styles.explainer}>
                {job.started.target.message}
              </Text>
            </>
          ) : null}
        </Card>

        {lines.length > 0 ? (
          <Card testID={`${testID}-progress-card`}>
            <Stamp tone="sub">{job.busy ? "IMPORTING" : "WHAT HAPPENED"}</Stamp>
            {lines.map((line) => (
              <Text key={line.index} testID={`${testID}-progress-${line.index}`} style={styles.progress}>
                {line.text}
              </Text>
            ))}
            {job.busy && liveChecks.length > 0 ? (
              <Stamp testID={`${testID}-live-checks`}>
                {liveChecks.filter((c) => c.status === "matched").length} OF {liveChecks.length}{" "}
                CHECKS MATCHING
              </Stamp>
            ) : null}
          </Card>
        ) : null}

        {job.error ? (
          <ErrorState message={job.error} testID={`${testID}-error`} onRetry={() => job.reset()} />
        ) : null}

        {done ? (
          <>
            <Report report={done.report} testID={`${testID}-report`} />
            {(done.diagnostics ?? []).length > 0 ? (
              <Card testID={`${testID}-diagnostics-card`}>
                <Stamp tone="sub">WHAT THE ENGINE AND THE HOST SAID</Stamp>
                <DiagnosticList
                  diagnostics={done.diagnostics}
                  testID={`${testID}-diagnostics`}
                />
              </Card>
            ) : null}
            <View style={styles.stampRow}>
              <WhatIfStamp whatIf={done.what_if} testID={`${testID}-whatif`} />
              <Stamp testID={`${testID}-provenance`}>
                ENGINE {done.engine_version.toUpperCase()}
                {done.revision ? ` · REV ${done.revision.slice(0, 7)}` : ""}
              </Stamp>
            </View>
          </>
        ) : null}

        {edit.pending ? (
          <ProposalCard
            proposal={edit.pending}
            busy={edit.busy}
            testID={`${testID}-proposal-card`}
            onApply={() => void edit.resolve("accept")}
            onDiscard={() => void edit.resolve("discard")}
          />
        ) : null}
        {edit.resolution ? (
          <Stamp testID={`${testID}-resolution`} tone="pine">
            {edit.resolution.kind.toUpperCase()}
          </Stamp>
        ) : null}
        {edit.error ? <ErrorState message={edit.error} testID={`${testID}-edit-error`} /> : null}

        <Card testID={`${testID}-export-card`}>
          <Stamp tone="sub">EXPORT</Stamp>
          <Text style={styles.explainer}>
            {mode === "budget"
              ? "The forecast as a workbook, one row per line and a closing balance row. It carries its own opening balance, so it can be imported back."
              : "The ledger as a workbook: every recorded row, as it stands."}
          </Text>
          <View style={styles.actions}>
            <Button
              label="Budget"
              testID={`${testID}-mode-budget`}
              variant={mode === "budget" ? "primary" : "secondary"}
              onPress={() => setMode("budget")}
            />
            <Button
              label="Ledger"
              testID={`${testID}-mode-ledger`}
              variant={mode === "ledger" ? "primary" : "secondary"}
              onPress={() => setMode("ledger")}
            />
          </View>
          {mode === "budget" ? (
            <>
              <View style={styles.actions}>
                {WINDOWS.map((window) => (
                  <Button
                    key={window}
                    label={`${window} months`}
                    testID={`${testID}-months-${window}`}
                    variant={months === window ? "primary" : "secondary"}
                    onPress={() => setMonths(window)}
                  />
                ))}
              </View>
              <TextInput
                testID={`${testID}-start`}
                accessibilityLabel="Start month"
                style={styles.input}
                value={start}
                onChangeText={setStart}
                placeholder="2026-01-01"
              />
            </>
          ) : null}
          <Button
            label={DELIVERY === "download" ? "Download" : "Share"}
            testID={`${testID}-download`}
            onPress={() => void download()}
          />
          {exportNote ? (
            <Text testID={`${testID}-export-note`} style={styles.progress}>
              {exportNote}
            </Text>
          ) : null}
          {exportError ? (
            <ErrorState message={exportError} testID={`${testID}-export-error`} />
          ) : null}
        </Card>
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
  headerRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  back: { fontFamily: font.mono, fontSize: 10, letterSpacing: 0.7, color: color.sub },
  title: { fontFamily: font.display, fontSize: 28, fontWeight: "600", color: color.ink, flex: 1 },
  body: { flex: 1 },
  bodyContent: { gap: 14, paddingBottom: 12 },
  explainer: { fontFamily: font.ui, fontSize: 12.5, color: color.sub },
  example: { fontFamily: font.mono, fontSize: 11, color: color.faint },
  refusal: { fontFamily: font.ui, fontSize: 13.5, color: color.ink },
  progress: { fontFamily: font.ui, fontSize: 12.5, color: color.sub },
  incomplete: { fontFamily: font.ui, fontSize: 12.5, color: color.rust },
  summary: { fontFamily: font.display, fontSize: 18, fontWeight: "600", color: color.ink },
  check: { gap: 4, width: "100%" },
  status: { fontFamily: font.mono, fontSize: 9, letterSpacing: 0.7 },
  figures: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
  sheetFigure: { fontFamily: font.mono, fontSize: 10, color: color.sub },
  arrow: { fontFamily: font.mono, fontSize: 10, color: color.faint },
  engineFigure: { fontFamily: font.ui, fontSize: 13, fontWeight: "600" },
  deltaFigure: { fontFamily: font.mono, fontSize: 10, color: color.rust },
  note: { fontFamily: font.ui, fontSize: 12, color: color.sub },
  stampRow: { flexDirection: "row", alignItems: "center", gap: 10, flexWrap: "wrap" },
  actions: { flexDirection: "row", gap: 10, width: "100%" },
  inline: { flexDirection: "row", gap: 10, width: "100%" },
  input: {
    height: 42,
    flex: 1,
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
});
