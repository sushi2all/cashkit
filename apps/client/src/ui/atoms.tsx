/**
 * The computed-receipt vocabulary (ADR-0023), as components.
 *
 * There are no chat bubbles in this app. There are quotes, receipts and cards,
 * and every computed figure is followed somewhere by a monospace stamp saying
 * where it came from. These are the pieces every screen is built from, so the
 * conventions stay identical as screens are added — which is the risk ADR-0023
 * names explicitly.
 */
import React from "react";
import { Text, View, Pressable, StyleSheet, type ViewStyle, type TextStyle } from "react-native";

import { color, font, radius } from "./tokens";

/** A tiny monospace provenance stamp: as-of, ids, revisions, engine version. */
export function Stamp({
  children,
  tone = "faint",
  testID,
}: {
  children: React.ReactNode;
  tone?: "faint" | "sub" | "pine" | "rust" | "ink";
  testID?: string;
}) {
  return (
    <Text testID={testID} style={[styles.stamp, { color: color[tone === "faint" ? "faint" : tone] }]}>
      {children}
    </Text>
  );
}

/** The screen eyebrow: book · scenario · as-of (SPEC §6, D-MLP-05a drops the chip). */
export function Eyebrow({ children, testID }: { children: React.ReactNode; testID?: string }) {
  return (
    <Text testID={testID} style={styles.eyebrow}>
      {children}
    </Text>
  );
}

/** The dotted leader that makes a list read as a receipt. */
export function Leader() {
  return (
    <View style={styles.leader} pointerEvents="none">
      <Text numberOfLines={1} style={styles.leaderDots}>
        {". ".repeat(120)}
      </Text>
    </View>
  );
}

/**
 * One receipt line: a label on the left, a figure on the right, dots between.
 *
 * `meta` is the small monospace line under the label — `item:rent · monthly`,
 * `event:one-off · travel` — which is how a row says which engine object it
 * came from.
 */
export function LeaderRow({
  label,
  meta,
  value,
  tone = "ink",
  emphasis = false,
  testID,
  onPress,
}: {
  label: React.ReactNode;
  meta?: React.ReactNode;
  value: React.ReactNode;
  tone?: "ink" | "rust" | "sub" | "pine";
  emphasis?: boolean;
  testID?: string;
  onPress?: () => void;
}) {
  const body = (
    <View style={styles.row} testID={testID}>
      <View style={styles.rowLeft}>
        <Text style={[styles.rowLabel, emphasis && styles.rowLabelStrong]}>{label}</Text>
        {meta ? <Text style={styles.rowMeta}>{meta}</Text> : null}
      </View>
      <Leader />
      <Text testID={testID ? `${testID}-value` : undefined} style={[styles.rowValue, { color: color[tone] }]}>
        {value}
      </Text>
    </View>
  );
  if (!onPress) return body;
  return (
    <Pressable onPress={onPress} accessibilityRole="button">
      {body}
    </Pressable>
  );
}

/** A white receipt card with a hairline border. */
export function Card({
  children,
  style,
  testID,
  tone = "default",
}: {
  children: React.ReactNode;
  style?: ViewStyle;
  testID?: string;
  tone?: "default" | "pending";
}) {
  return (
    <View
      testID={testID}
      style={[styles.card, tone === "pending" && styles.cardPending, style]}
    >
      {children}
    </View>
  );
}

export function Divider({ strong = false }: { strong?: boolean }) {
  return <View style={strong ? styles.dividerStrong : styles.divider} />;
}

export function Button({
  label,
  onPress,
  variant = "secondary",
  disabled = false,
  testID,
}: {
  label: string;
  onPress: () => void;
  variant?: "primary" | "secondary" | "quiet";
  disabled?: boolean;
  testID?: string;
}) {
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        variant === "primary" ? styles.buttonPrimary : styles.buttonSecondary,
        variant === "quiet" && styles.buttonQuiet,
        pressed && styles.buttonPressed,
        disabled && styles.buttonDisabled,
      ]}
    >
      <Text style={[styles.buttonLabel, variant === "primary" && styles.buttonLabelPrimary]}>
        {label}
      </Text>
    </Pressable>
  );
}

/** The verdict line at the top of an answer card — the sentence, set in serif. */
export function Verdict({ children, testID }: { children: React.ReactNode; testID?: string }) {
  return (
    <Text testID={testID} style={styles.verdict}>
      {children}
    </Text>
  );
}

/** The user's own words. An editorial quote, never a bubble (ADR-0023). */
export function QuoteRow({ children, testID }: { children: React.ReactNode; testID?: string }) {
  return (
    <View style={styles.quoteRow}>
      <View style={styles.quoteBar} />
      <Text testID={testID} style={styles.quoteText}>
        {children}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  stamp: { fontFamily: font.mono, fontSize: 9, letterSpacing: 0.7 } as TextStyle,
  eyebrow: { fontFamily: font.mono, fontSize: 10, letterSpacing: 0.7, color: color.sub } as TextStyle,
  leader: { flex: 1, height: 12, overflow: "hidden", marginHorizontal: 8, justifyContent: "flex-end" },
  leaderDots: { fontFamily: font.mono, fontSize: 9, letterSpacing: 3, color: color.dotted },
  row: { flexDirection: "row", alignItems: "flex-end", width: "100%" },
  rowLeft: { flexShrink: 1 },
  rowLabel: { fontFamily: font.ui, fontSize: 13, color: color.sub },
  rowLabelStrong: { fontSize: 14, fontWeight: "600", color: color.ink },
  rowMeta: { fontFamily: font.mono, fontSize: 8.5, color: color.faint, marginTop: 3 },
  rowValue: { fontFamily: font.ui, fontSize: 15, fontWeight: "600" },
  card: {
    backgroundColor: color.card,
    borderRadius: radius.card,
    borderWidth: 1,
    borderColor: color.hair,
    padding: 18,
    gap: 12,
    width: "100%",
  },
  cardPending: { borderColor: "#B9BDB3", paddingVertical: 16, gap: 10 },
  divider: { height: 1, backgroundColor: color.hairSoft, width: "100%" },
  dividerStrong: { height: 1, backgroundColor: color.ink, width: "100%" },
  button: {
    flex: 1,
    height: 44,
    borderRadius: radius.card,
    alignItems: "center",
    justifyContent: "center",
  },
  buttonPrimary: { backgroundColor: color.pine },
  buttonSecondary: { borderWidth: 1, borderColor: color.hair },
  buttonQuiet: { borderWidth: 0 },
  buttonPressed: { opacity: 0.75 },
  buttonDisabled: { opacity: 0.4 },
  buttonLabel: { fontFamily: font.ui, fontSize: 14, fontWeight: "600", color: color.ink },
  buttonLabelPrimary: { color: "#FFFFFF" },
  verdict: { fontFamily: font.display, fontSize: 19, fontWeight: "600", color: color.ink },
  quoteRow: { flexDirection: "row", width: "100%" },
  quoteBar: { width: 2, backgroundColor: color.pine, marginRight: 12 },
  quoteText: { fontFamily: font.display, fontSize: 18, color: color.ink, flexShrink: 1 },
});
