/**
 * The standing-warnings banner (SPEC §6-S1, D-MLP-05(b)).
 *
 * Warnings are structural and always on: there are no configurable thresholds
 * in the MLP, and nothing waits for a background job. The service computes
 * them at every update and ships them on the state payload; this element
 * renders them and hides itself when the book is clear.
 *
 * Every figure here — the depth of a negative month, the minimum — is a
 * service string. The banner counts months, which is counting rows, not
 * computing money.
 */
import React from "react";
import { Text, View, StyleSheet } from "react-native";

import type { Warnings } from "@cashkit/api-types";

import { formatMoney } from "../../money/money";
import { Stamp } from "../../ui/atoms";
import { monthLabel } from "../../ui/provenance";
import { color, font } from "../../ui/tokens";

export function WarningsBanner({
  warnings,
  testID = "warnings-banner",
}: {
  warnings: Warnings | null | undefined;
  testID?: string;
}) {
  const negatives = warnings?.negative_months ?? [];
  if (!warnings || negatives.length === 0) return null;
  const first = negatives[0];

  return (
    <View testID={testID} style={styles.banner}>
      <Stamp tone="rust" testID={`${testID}-headline`}>
        {negatives.length === 1
          ? "1 MONTH GOES BELOW ZERO"
          : `${negatives.length} MONTHS GO BELOW ZERO`}
      </Stamp>
      {first ? (
        <Text testID={`${testID}-first`} style={styles.detail}>
          First in {monthLabel(first.period)}, down to {formatMoney(first.depth)}.
        </Text>
      ) : null}
      <Text testID={`${testID}-min`} style={styles.detail}>
        Lowest cash {formatMoney(warnings.min_cash)}
        {warnings.min_cash_period ? ` in ${monthLabel(warnings.min_cash_period)}` : ""}.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    width: "100%",
    borderLeftWidth: 2,
    borderLeftColor: color.rust,
    paddingLeft: 10,
    paddingVertical: 6,
    gap: 3,
  },
  detail: { fontFamily: font.ui, fontSize: 12, color: color.ink },
});
