/**
 * Screen 15 — Settings, privacy, about and history (SPEC §6-S15).
 *
 * There is no `design.pen` screen for this one; SPEC §6-S15's element
 * inventory is the specification, and the receipt vocabulary of ADR-0023 is
 * the same here as everywhere else.
 *
 * Four things live here and one of them is a trap.
 *
 *  * **Book settings are writes.** Horizon and opening balance are the
 *    `set_horizon` and `set_opening_balance` host ops (SPEC §2.5, D-MLP-03),
 *    and they go through `POST /book/edits` like every other change. There is
 *    no settings form that saves. There is a settings form that proposes.
 *  * **There are no thresholds.** D-MLP-05(b) replaced threshold alerts with
 *    structural warnings computed at every update, so there is nothing to
 *    configure and nothing is scaffolded for it.
 *  * **Deleting the account needs the phrase typed.** `DELETE /me` erases the
 *    Postgres rows and the book directory (SPEC §9, D-MLP-22). It is not
 *    undoable, so it is not a button you can hit by accident.
 *  * **The revision history is read-only** (R12). SPEC §2.4: "History (R12)
 *    lists revisions read-only; time travel UIs beyond the list are post-MLP."
 *    Nothing here restores, checks out or reverts.
 *
 * The subprocessor list is the compliance workstream's content (SPEC §9); this
 * screen is the surface it lands on, and it says so rather than inventing
 * vendor names.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ScrollView, Text, TextInput, View, StyleSheet } from "react-native";

import type { HistoryResponse, Me } from "@cashkit/api-types";

import { api, describeError } from "../api/client";
import { formatMoney, moneyTone } from "../money/money";
import { useBook } from "../state/book";
import { useEditProposal } from "../state/edits";
import { useSession } from "../state/session";
import { Button, Card, Divider, LeaderRow, Stamp } from "../ui/atoms";
import { AsOfLine, shortDate } from "../ui/provenance";
import { EmptyState, ErrorState, LoadingState } from "../ui/states";
import { color, font, radius, space } from "../ui/tokens";
import { ProposalCard } from "./components/ProposalCard";

/**
 * The SPEC §9 subprocessor list, rendered from `compliance/subprocessors.md`.
 *
 * It is a literal rather than a fetch: the list changes when a contract
 * changes, not at runtime, and a privacy disclosure that could fail to load is
 * a privacy disclosure that is sometimes absent. Keeping it in the bundle
 * means it renders offline, on the first paint, with no spinner.
 *
 * `apps/service/tests/test_compliance.py` greps the source for outbound hosts
 * and fails on one this page does not name, so the two cannot drift.
 */
const SUBPROCESSORS: ReadonlyArray<{
  key: string;
  name: string;
  purpose: string;
  region: string;
}> = [
  { key: "hetzner", name: "Hetzner", purpose: "hosting, disk and backup storage", region: "EU" },
  { key: "openrouter", name: "OpenRouter", purpose: "routes each assistant request", region: "US" },
  { key: "google", name: "Google", purpose: "the assistant model itself", region: "Google regions" },
  { key: "sentry", name: "Sentry", purpose: "errors only, never your text or figures", region: "EU" },
  { key: "grafana", name: "Grafana Labs", purpose: "counts and timings, no identifiers", region: "EU" },
];

/**
 * The retention sentence, which SPEC §9 requires the privacy page to state.
 * The same three numbers are settings the service enforces, and a test
 * compares the policy document against them.
 */
const RETENTION =
  "Raw assistant requests are deleted after 30 days, request logs after 90, and backups after 30. " +
  "Deleting your account removes everything immediately and clears the backups within 30 days.";

const DECIMAL = /^-?\d+(\.\d{1,4})?$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
/** Typed exactly, or the account is not deleted. */
const DELETE_PHRASE = "delete my account";

export function SettingsScreen({ onBack, testID = "settings-screen" }: { onBack: () => void; testID?: string }) {
  const book = useBook();
  const session = useSession();

  const [me, setMe] = useState<Me | null>(null);
  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [horizonStart, setHorizonStart] = useState("");
  const [horizonEnd, setHorizonEnd] = useState("");
  const [opening, setOpening] = useState("");
  const [cutover, setCutover] = useState("");
  const [phrase, setPhrase] = useState("");
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const [profile, revisions] = await Promise.all([
      api.GET("/me", {}),
      api.GET("/book/history", { params: { query: { limit: 50 } } }),
    ]);
    setLoading(false);
    if (profile.error || !profile.data) {
      setError(describeError(profile.error, profile.response.status));
      return;
    }
    setError(null);
    setMe(profile.data);
    setHistory(revisions.data ?? null);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const edit = useEditProposal({
    onApplied: async () => {
      await book.refresh();
      await load();
    },
  });

  const state = book.state;

  useEffect(() => {
    if (!state) return;
    setHorizonStart(state.book.horizon_start);
    setHorizonEnd(state.book.horizon_end);
    setOpening(state.book.opening_balance.exact);
    setCutover(state.book.cutover);
  }, [state]);

  const proposeHorizon = useCallback(async () => {
    await edit.propose(
      [{ op: "set_horizon", start: horizonStart.trim(), end: horizonEnd.trim() }],
      { origin: "settings" },
    );
  }, [edit, horizonStart, horizonEnd]);

  const proposeOpening = useCallback(async () => {
    await edit.propose([{ op: "set_opening_balance", amount: opening.trim() }], {
      origin: "settings",
    });
  }, [edit, opening]);

  const proposeCutover = useCallback(async () => {
    await edit.propose([{ op: "set_cutover", date: cutover.trim() }], { origin: "settings" });
  }, [edit, cutover]);

  const deleteAccount = useCallback(async () => {
    setDeleting(true);
    const { error: err, response } = await api.DELETE("/me", {});
    setDeleting(false);
    if (err) {
      setError(describeError(err, response.status));
      return;
    }
    await session.signOut();
  }, [session]);

  const exportData = useCallback(() => {
    // The archive is a download the browser performs, not a payload the client
    // assembles. `GET /me/export` is the whole of it (SPEC §3, §9).
    void api.GET("/me/export", {});
  }, []);

  if (loading && !me) return <LoadingState label="Reading your account…" />;
  if (error && !me) return <ErrorState message={error} onRetry={() => void load()} testID={`${testID}-error`} />;
  if (!me) return <EmptyState title="No account." example="sign in again" />;

  const horizonReady = ISO_DATE.test(horizonStart.trim()) && ISO_DATE.test(horizonEnd.trim());
  const openingReady = DECIMAL.test(opening.trim());
  const cutoverReady = ISO_DATE.test(cutover.trim());
  const phraseMatches = phrase.trim().toLowerCase() === DELETE_PHRASE;

  return (
    <View testID={testID} style={styles.screen}>
      <View style={styles.headerRow}>
        <Text testID={`${testID}-back`} style={styles.back} onPress={onBack}>
          ‹ BACK
        </Text>
        <Text style={styles.title}>Settings</Text>
      </View>

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        <Card testID={`${testID}-account-card`}>
          <Stamp tone="sub">ACCOUNT</Stamp>
          <LeaderRow
            testID={`${testID}-email`}
            label="Email"
            value={<Text style={styles.engineWords}>{me.email}</Text>}
            tone="sub"
          />
          <LeaderRow
            label="Since"
            value={<Text style={styles.engineWords}>{shortDate(me.created_at)}</Text>}
            tone="sub"
          />
          <View style={styles.actions}>
            <Button label="Export my data" testID={`${testID}-export`} onPress={exportData} />
            <Button label="Sign out" testID={`${testID}-signout`} onPress={() => void session.signOut()} />
          </View>
        </Card>

        {state ? (
          <Card testID={`${testID}-book-card`}>
            <Stamp tone="sub">BOOK · EVERY CHANGE IS A PROPOSAL</Stamp>

            <Text style={styles.fieldLabel}>Horizon</Text>
            <View style={styles.inline}>
              <TextInput
                testID={`${testID}-horizon-start`}
                accessibilityLabel="Horizon start"
                style={styles.input}
                value={horizonStart}
                onChangeText={setHorizonStart}
              />
              <TextInput
                testID={`${testID}-horizon-end`}
                accessibilityLabel="Horizon end"
                style={styles.input}
                value={horizonEnd}
                onChangeText={setHorizonEnd}
              />
            </View>
            <Button
              label="Propose the horizon"
              testID={`${testID}-horizon-submit`}
              disabled={edit.busy || !horizonReady}
              onPress={() => void proposeHorizon()}
            />

            <Divider />
            <Text style={styles.fieldLabel}>
              {`Opening balance · now ${formatMoney(state.book.opening_balance)}`}
            </Text>
            <TextInput
              testID={`${testID}-opening`}
              accessibilityLabel="Opening balance"
              style={styles.input}
              value={opening}
              onChangeText={setOpening}
            />
            <Button
              label="Propose the opening balance"
              testID={`${testID}-opening-submit`}
              disabled={edit.busy || !openingReady}
              onPress={() => void proposeOpening()}
            />

            <Divider />
            <Text style={styles.fieldLabel}>
              {`Cutover · now ${shortDate(state.book.cutover)} · the ledger is authoritative before it`}
            </Text>
            <TextInput
              testID={`${testID}-cutover`}
              accessibilityLabel="Cutover date"
              style={styles.input}
              value={cutover}
              onChangeText={setCutover}
            />
            <Button
              label="Propose the cutover"
              testID={`${testID}-cutover-submit`}
              disabled={edit.busy || !cutoverReady}
              onPress={() => void proposeCutover()}
            />
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

        {/* R12 — the read-only revision list. */}
        <Card testID={`${testID}-history-card`}>
          <Stamp tone="sub" testID={`${testID}-history-label`}>
            HISTORY · READ-ONLY
          </Stamp>
          {!history || history.revisions.length === 0 ? (
            <Stamp testID={`${testID}-history-empty`}>NO REVISION HAS BEEN RECORDED YET</Stamp>
          ) : (
            history.revisions.map((revision) => (
              <LeaderRow
                key={revision.id}
                testID={`${testID}-revision-${revision.id.slice(0, 7)}`}
                label={revision.message || "(no message)"}
                meta={`${revision.author} · ${revision.timestamp}${
                  revision.engine_version ? ` · engine v${revision.engine_version}` : ""
                }`}
                value={<Text style={styles.engineWords}>{revision.id.slice(0, 7)}</Text>}
                tone="sub"
              />
            ))
          )}
        </Card>

        <Card testID={`${testID}-about-card`}>
          <Stamp tone="sub">ABOUT</Stamp>
          <LeaderRow
            testID={`${testID}-engine-version`}
            label="Engine"
            value={
              <Text style={styles.engineWords}>
                {`v${state?.engine_version ?? history?.engine_version ?? "—"} · DETERMINISTIC`}
              </Text>
            }
            tone="sub"
          />
          <LeaderRow
            testID={`${testID}-revision`}
            label="Current revision"
            value={
              <Text style={styles.engineWords}>
                {state?.revision ? state.revision.slice(0, 7) : "UNCOMMITTED"}
              </Text>
            }
            tone="sub"
          />
          <LeaderRow
            label="Rounding"
            value={<Text style={styles.engineWords}>CANONICAL ORDER · 4DP</Text>}
            tone="sub"
          />
          {state ? (
            <LeaderRow
              label="Opening balance"
              value={formatMoney(state.book.opening_balance)}
              tone={moneyTone(state.book.opening_balance)}
            />
          ) : null}
        </Card>

        <Card testID={`${testID}-privacy-card`}>
          <Stamp tone="sub">PRIVACY</Stamp>
          <Text style={styles.explainer}>
            Everything you write is stored in the EU. These are the companies that process any part
            of it, and what each one can see. It is the whole list, and we will tell you before we
            add to it.
          </Text>
          {/* SPEC §9 requires the list to be published here before any external
              user. The source of truth is `compliance/subprocessors.md`; this
              is the rendering of it, and a service test fails if the code ever
              talks to a host that page does not name. */}
          <View testID={`${testID}-subprocessors`}>
            {SUBPROCESSORS.map((entry) => (
              <LeaderRow
                key={entry.name}
                label={entry.name}
                value={entry.region}
                meta={entry.purpose}
                testID={`${testID}-subprocessor-${entry.key}`}
              />
            ))}
          </View>
          <Divider />
          <Text style={styles.explainer} testID={`${testID}-subprocessors-absent`}>
            Not on the list, on purpose: no database vendor (Postgres runs on our own machine), no
            speech-recognition vendor (dictation runs on your device, and is switched off rather
            than sent anywhere), no prompt-analytics platform, and no bank aggregator.
          </Text>
          <Divider />
          <Text style={styles.explainer} testID={`${testID}-retention`}>
            {RETENTION}
          </Text>
        </Card>

        <Card testID={`${testID}-delete-card`}>
          <Stamp tone="rust">DELETE ACCOUNT</Stamp>
          <Text style={styles.explainer}>
            {`This erases your book, its history and every row about you. It cannot be undone. Type “${DELETE_PHRASE}” to confirm.`}
          </Text>
          <TextInput
            testID={`${testID}-delete-phrase`}
            accessibilityLabel="Type the confirmation phrase"
            style={styles.input}
            placeholder={DELETE_PHRASE}
            placeholderTextColor={color.faint}
            value={phrase}
            onChangeText={setPhrase}
          />
          <Button
            label={deleting ? "Deleting…" : "Delete my account"}
            testID={`${testID}-delete-submit`}
            disabled={deleting || !phraseMatches}
            onPress={() => void deleteAccount()}
          />
        </Card>
      </ScrollView>

      {state ? (
        <View style={styles.footer}>
          <AsOfLine asOf={state.as_of} scenario={state.scenario} testID={`${testID}-as-of`} />
        </View>
      ) : null}
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
  engineWords: { fontFamily: font.mono, fontSize: 10, color: color.ink },
  fieldLabel: { fontFamily: font.mono, fontSize: 9, letterSpacing: 0.7, color: color.sub },
  explainer: { fontFamily: font.ui, fontSize: 12.5, color: color.sub },
  inline: { flexDirection: "row", gap: 10, width: "100%" },
  actions: { flexDirection: "row", gap: 10, width: "100%" },
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
  footer: { gap: 6 },
});
