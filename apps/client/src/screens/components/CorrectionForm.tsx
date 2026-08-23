/**
 * "Record a correction" (ADR-0012, ADR-0013, SPEC §6-S7).
 *
 * An actual is a fact and the record of it can be wrong; correcting the record
 * is itself an event — dated, attributed, auditable. So this form does not
 * edit anything. It composes one M6 operation and hands it to the ordinary
 * proposal pipeline, where the user confirms it like every other write.
 *
 * **The note is mandatory and the form enforces it too.** The service does
 * (`CorrectActual.note` has `min_length=1`), and a client that let the user
 * press the button and then showed them a rejection would be teaching them the
 * note is a formality. It is the reason the correction is auditable at all: "a
 * correction without a stated reason is not auditable" (ADR-0012).
 *
 * The original stays on the screen above, struck. This form never replaces it.
 *
 * **The amount is checked for shape, not for value.** The operation schema
 * takes money as a decimal *string* precisely so no float ever enters the money
 * path, and a string that is not a decimal is rejected by the request schema
 * with a 422 — which would reach the user as a transport error rather than as
 * guidance. So the form requires the digits to look like digits before it will
 * send. It is a character test on what was typed: nothing is parsed, nothing is
 * rounded, nothing is compared, and the string the user wrote is the string the
 * service receives.
 */
import React, { useState } from "react";
import { Text, TextInput, View, StyleSheet } from "react-native";

import type { LedgerEvent } from "@cashkit/api-types";

import { formatMoney } from "../../money/money";
import { Button, Card, Stamp } from "../../ui/atoms";
import { shortDate } from "../../ui/provenance";
import { color, font, radius } from "../../ui/tokens";

/**
 * What a decimal string looks like. Up to 4 places, which is the engine's own
 * precision (D-MLP-06); anything longer would be refused rather than rounded.
 */
const DECIMAL = /^-?\d+(\.\d{1,4})?$/;

export function CorrectionForm({
  event,
  busy = false,
  onSubmit,
  onCancel,
  testID = "correction-form",
}: {
  event: LedgerEvent;
  busy?: boolean;
  /** Amount as the user typed it — a decimal string, never a number. */
  onSubmit: (amount: string, note: string) => void;
  onCancel: () => void;
  testID?: string;
}) {
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const typed = amount.trim();
  const looksDecimal = DECIMAL.test(typed);
  const ready = looksDecimal && note.trim().length > 0;

  return (
    <Card tone="pending" testID={testID}>
      <Stamp tone="sub" testID={`${testID}-label`}>
        RECORD A CORRECTION · APPEND-ONLY
      </Stamp>

      <Text testID={`${testID}-original`} style={styles.original}>
        {`${event.note || event.item || "entry"} · ${shortDate(event.date)} · recorded as ${formatMoney(
          event.amount,
        )}`}
      </Text>
      <Stamp testID={`${testID}-original-meta`}>{`event:${event.id}`}</Stamp>

      <View style={styles.field}>
        <Text style={styles.fieldLabel}>What it should say</Text>
        <TextInput
          testID={`${testID}-amount`}
          accessibilityLabel="Corrected amount"
          style={styles.input}
          placeholder="-69.00"
          placeholderTextColor={color.faint}
          value={amount}
          onChangeText={setAmount}
        />
      </View>

      <View style={styles.field}>
        <Text style={styles.fieldLabel}>Why (required)</Text>
        <TextInput
          testID={`${testID}-note`}
          accessibilityLabel="Reason for the correction, required"
          style={styles.input}
          placeholder="typo when it was entered"
          placeholderTextColor={color.faint}
          value={note}
          onChangeText={setNote}
        />
      </View>

      {typed.length > 0 && !looksDecimal ? (
        <Text testID={`${testID}-amount-shape`} style={styles.required}>
          Write the corrected amount in figures, like −69.00 or 69. Expenses are negative.
        </Text>
      ) : null}

      {looksDecimal && note.trim().length === 0 ? (
        <Text testID={`${testID}-note-required`} style={styles.required}>
          A correction needs a reason. The original stays on the record either way.
        </Text>
      ) : null}

      <View style={styles.actions}>
        <Button label="Cancel" testID={`${testID}-cancel`} disabled={busy} onPress={onCancel} />
        <Button
          label="Record it"
          variant="primary"
          testID={`${testID}-submit`}
          disabled={busy || !ready}
          onPress={() => onSubmit(amount.trim(), note.trim())}
        />
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  original: { fontFamily: font.ui, fontSize: 13, color: color.ink },
  field: { width: "100%", gap: 4 },
  fieldLabel: { fontFamily: font.mono, fontSize: 9, letterSpacing: 0.7, color: color.sub },
  input: {
    height: 42,
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
  required: { fontFamily: font.ui, fontSize: 11, color: color.sub },
  actions: { flexDirection: "row", gap: 10, width: "100%" },
});
