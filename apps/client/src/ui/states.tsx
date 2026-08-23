/**
 * Empty, loading and error states.
 *
 * SPEC §6 requires all three on every screen, and requires an empty state to
 * carry one example ask. They are components rather than ad-hoc markup so no
 * screen can quietly ship without them.
 */
import React from "react";
import { ActivityIndicator, Text, View, StyleSheet } from "react-native";

import { Button } from "./atoms";
import { color, font } from "./tokens";

export function LoadingState({ label = "Computing…", testID = "loading" }: { label?: string; testID?: string }) {
  return (
    <View testID={testID} style={styles.centre}>
      <ActivityIndicator color={color.pine} />
      <Text style={styles.muted}>{label}</Text>
    </View>
  );
}

export function EmptyState({
  title,
  example,
  testID = "empty",
}: {
  title: string;
  /** The one example ask SPEC §6 requires. */
  example: string;
  testID?: string;
}) {
  return (
    <View testID={testID} style={styles.centre}>
      <Text style={styles.emptyTitle}>{title}</Text>
      <Text testID={`${testID}-example`} style={styles.example}>
        try: {example}
      </Text>
    </View>
  );
}

export function ErrorState({
  message,
  onRetry,
  testID = "error",
}: {
  message: string;
  onRetry?: () => void;
  testID?: string;
}) {
  return (
    <View testID={testID} style={styles.centre}>
      <Text testID={`${testID}-message`} style={styles.errorText}>
        {message}
      </Text>
      {onRetry ? (
        <View style={styles.retry}>
          <Button label="Try again" onPress={onRetry} testID={`${testID}-retry`} />
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  centre: { alignItems: "center", justifyContent: "center", gap: 10, paddingVertical: 32, width: "100%" },
  muted: { fontFamily: font.ui, fontSize: 13, color: color.sub },
  emptyTitle: { fontFamily: font.display, fontSize: 18, color: color.ink, textAlign: "center" },
  example: { fontFamily: font.mono, fontSize: 11, color: color.faint, textAlign: "center" },
  errorText: { fontFamily: font.ui, fontSize: 14, color: color.rust, textAlign: "center" },
  retry: { flexDirection: "row", width: 160 },
});
