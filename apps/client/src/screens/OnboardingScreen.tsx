/**
 * Screen 13 — the first book (SPEC §6-S13).
 *
 * Three steps, and the shape of them is the point:
 *
 *  (a) horizon and opening balance → `POST /books` creates the **empty** book
 *      immediately, so `turns.book_id` is never null;
 *  (b) an optional "describe your money in a few sentences" free text, which is
 *      an ordinary `POST /turns` against that book and comes back as an
 *      ordinary proposal card;
 *  (c) apply → the book is populated → Home.
 *
 * **It never produces a silent book.** A book that arrives with lines in it
 * that nobody confirmed would be the ADR-0029 violation this whole product is
 * built to make impossible — and it would be the first thing a new user sees.
 * So step (b) is a turn like any other and step (c) is a confirmation like any
 * other: the same `POST /turns`, the same `ProposalCard`, the same
 * `POST /proposals/{id}`. Skipping after (a) leaves an empty book, which is a
 * real and honest state.
 *
 * No `design.pen` reference; SPEC §6-S13's element inventory is the
 * specification.
 */
import React, { useCallback, useMemo, useState } from "react";
import { ScrollView, Text, TextInput, View, StyleSheet } from "react-native";

import { api, describeError } from "../api/client";
import { useBook } from "../state/book";
import { useConversation } from "../state/conversation";
import { Button, Card, Divider, LeaderRow, Stamp, Verdict } from "../ui/atoms";
import { DiagnosticList } from "../ui/provenance";
import { ErrorState, LoadingState } from "../ui/states";
import { color, font, radius, space } from "../ui/tokens";
import { ProposalCard } from "./components/ProposalCard";

const DECIMAL = /^-?\d+(\.\d{1,4})?$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** The example ask SPEC §6 requires, and the placeholder for step (b). */
const EXAMPLE =
  "I earn 2 400 a month, rent is 900, and I put 200 aside every month from March";

type Step = "book" | "describe" | "done";

export function OnboardingScreen({
  onFinished,
  testID = "onboarding-screen",
}: {
  onFinished: () => void;
  testID?: string;
}) {
  const book = useBook();
  const conversation = useConversation();

  const [step, setStep] = useState<Step>("book");
  const [start, setStart] = useState("2026-01-01");
  const [end, setEnd] = useState("2027-01-01");
  const [opening, setOpening] = useState("0.00");
  const [text, setText] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = ISO_DATE.test(start.trim()) && ISO_DATE.test(end.trim()) && DECIMAL.test(opening.trim());

  const createBook = useCallback(async () => {
    setCreating(true);
    setError(null);
    const { error: err, response } = await api.POST("/books", {
      body: {
        horizon_start: start.trim(),
        horizon_end: end.trim(),
        opening_balance: opening.trim(),
        // EUR only, monthly only (SPEC §1). The schema states them, so the
        // request states them; there is no picker for either in the MLP.
        currency: "EUR",
        grain: "month",
      },
    });
    setCreating(false);
    if (err) {
      setError(describeError(err, response.status));
      return;
    }
    await book.refresh();
    setStep("describe");
  }, [book, start, end, opening]);

  const describe = useCallback(async () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    await conversation.ask(trimmed);
  }, [conversation, text]);

  /** Newest first, so "the last turn" is a scan and not an index dance. */
  const reversed = useMemo(() => [...conversation.entries].reverse(), [conversation.entries]);

  /** The card raised by step (b), if the turn produced one. */
  const pending = useMemo(() => {
    const id = conversation.pendingProposalId;
    if (!id) return null;
    for (const entry of reversed) {
      if (entry.kind === "turn" && entry.response.proposal?.id === id) return entry.response.proposal;
      if (entry.kind === "resolution" && entry.response.proposal.id === id) return entry.response.proposal;
    }
    return null;
  }, [reversed, conversation.pendingProposalId]);

  /** What the last turn said, in whichever of the four kinds it came back as. */
  const lastTurn = useMemo(() => {
    for (const entry of reversed) {
      if (entry.kind === "turn") return entry.response;
      if (entry.kind === "failure") return null;
    }
    return null;
  }, [reversed]);

  const lastFailure = useMemo(() => {
    for (const entry of reversed) {
      if (entry.kind === "failure") return entry.message;
      if (entry.kind === "turn") return null;
    }
    return null;
  }, [reversed]);

  const lastResolution = useMemo(() => {
    for (const entry of reversed) {
      if (entry.kind === "resolution") return entry.response;
    }
    return null;
  }, [reversed]);

  const apply = useCallback(async () => {
    if (!pending) return;
    await conversation.resolve(pending.id, "accept");
    await book.refresh();
  }, [book, conversation, pending]);

  const discard = useCallback(async () => {
    if (!pending) return;
    await conversation.resolve(pending.id, "discard");
  }, [conversation, pending]);

  if (creating) return <LoadingState label="Making your book…" testID={`${testID}-creating`} />;

  const applied = lastResolution?.kind === "applied";

  return (
    <View testID={testID} style={styles.screen}>
      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        <Stamp tone="sub" testID={`${testID}-step`}>
          {step === "book" ? "STEP 1 OF 3 · YOUR BOOK" : applied ? "STEP 3 OF 3 · DONE" : "STEP 2 OF 3 · YOUR MONEY"}
        </Stamp>
        <Verdict testID={`${testID}-title`}>
          {step === "book" ? "Let's set up your book." : applied ? "Your book is ready." : "Describe your money."}
        </Verdict>

        {step === "book" ? (
          <Card testID={`${testID}-book-card`}>
            <Text style={styles.explainer}>
              Two things to start: how far ahead you want to look, and what you have today. Both
              can be changed later, from Settings, and both are changed the same way everything
              else is — as a card you confirm.
            </Text>

            <Text style={styles.fieldLabel}>Horizon</Text>
            <View style={styles.inline}>
              <TextInput
                testID={`${testID}-horizon-start`}
                accessibilityLabel="Horizon start"
                style={styles.input}
                value={start}
                onChangeText={setStart}
                placeholder="2026-01-01"
              />
              <TextInput
                testID={`${testID}-horizon-end`}
                accessibilityLabel="Horizon end"
                style={styles.input}
                value={end}
                onChangeText={setEnd}
                placeholder="2027-01-01"
              />
            </View>
            <Stamp>THE END IS EXCLUSIVE · EUR · MONTHLY</Stamp>

            <Text style={styles.fieldLabel}>What you have today</Text>
            <TextInput
              testID={`${testID}-opening`}
              accessibilityLabel="Opening balance"
              style={styles.input}
              value={opening}
              onChangeText={setOpening}
              placeholder="0.00"
            />

            <Button
              label="Make my book"
              variant="primary"
              testID={`${testID}-create`}
              disabled={!ready}
              onPress={() => void createBook()}
            />
            {!ready ? (
              <Text testID={`${testID}-hint`} style={styles.hint}>
                Dates read 2026-01-01, and the balance is a plain number like 1250.00.
              </Text>
            ) : null}
            {error ? <ErrorState message={error} testID={`${testID}-error`} /> : null}
          </Card>
        ) : (
          <Card testID={`${testID}-describe-card`}>
            <Text style={styles.explainer}>
              Say what comes in and what goes out, in your own words. The engine turns it into
              lines and shows you exactly what it understood — nothing is added to your book until
              you apply it.
            </Text>
            <TextInput
              testID={`${testID}-text`}
              accessibilityLabel="Describe your money"
              style={styles.textarea}
              value={text}
              onChangeText={setText}
              placeholder={EXAMPLE}
              multiline
            />
            <Text testID={`${testID}-example`} style={styles.example}>
              try: {EXAMPLE}
            </Text>
            <View style={styles.actions}>
              <Button
                label="Skip for now"
                testID={`${testID}-skip`}
                onPress={onFinished}
              />
              <Button
                label={conversation.busy ? "Working…" : "See what that means"}
                variant="primary"
                testID={`${testID}-send`}
                disabled={conversation.busy || text.trim().length === 0}
                onPress={() => void describe()}
              />
            </View>
          </Card>
        )}

        {lastFailure ? (
          <ErrorState message={lastFailure} testID={`${testID}-turn-error`} />
        ) : null}

        {lastTurn && lastTurn.kind !== "proposal" ? (
          <Card testID={`${testID}-reply-card`}>
            <Stamp tone="sub">{lastTurn.kind.toUpperCase()}</Stamp>
            <Text testID={`${testID}-reply`} style={styles.reply}>
              {lastTurn.clarification ?? lastTurn.reply}
            </Text>
            <DiagnosticList diagnostics={lastTurn.diagnostics} testID={`${testID}-diagnostics`} />
          </Card>
        ) : null}

        {pending ? (
          <ProposalCard
            proposal={pending}
            busy={conversation.busy}
            testID={`${testID}-proposal-card`}
            onApply={() => void apply()}
            onDiscard={() => void discard()}
          />
        ) : null}

        {applied ? (
          <Card testID={`${testID}-done-card`}>
            <Stamp tone="pine">APPLIED</Stamp>
            <LeaderRow
              testID={`${testID}-done-lines`}
              label="Lines in your book"
              value={String(book.state?.items.length ?? 0)}
              emphasis
            />
            <Divider />
            <Text style={styles.explainer}>
              Everything from here on works the same way: you say what changed, and you confirm a
              card before anything moves.
            </Text>
            <Button
              label="Go to my book"
              variant="primary"
              testID={`${testID}-finish`}
              onPress={onFinished}
            />
          </Card>
        ) : null}

        {step === "describe" && !applied ? (
          <Text testID={`${testID}-empty-note`} style={styles.hint}>
            You can skip this and add things one at a time later. An empty book is a real book.
          </Text>
        ) : null}
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
  body: { flex: 1 },
  bodyContent: { gap: 14, paddingBottom: 12 },
  explainer: { fontFamily: font.ui, fontSize: 12.5, color: color.sub },
  fieldLabel: { fontFamily: font.mono, fontSize: 9, letterSpacing: 0.7, color: color.sub },
  hint: { fontFamily: font.ui, fontSize: 12, color: color.faint },
  example: { fontFamily: font.mono, fontSize: 11, color: color.faint },
  reply: { fontFamily: font.ui, fontSize: 14, color: color.ink },
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
  textarea: {
    minHeight: 96,
    borderRadius: radius.card,
    borderWidth: 1,
    borderColor: color.hair,
    backgroundColor: color.card,
    padding: 12,
    fontFamily: font.ui,
    fontSize: 14,
    color: color.ink,
    outlineStyle: "none",
  } as object,
});
