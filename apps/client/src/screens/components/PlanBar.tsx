/**
 * The percent-of-plan bar of SPEC §6-S8, with the plan tick at 100%.
 *
 * Two rules, both from SPEC §5-F5, and the second is the one worth guarding:
 *
 *  * The bar encodes **percent of plan** and the tick marks where the plan
 *    sits, so a row that is over budget is longer than its tick. The amounts
 *    stay on the row as text — the bar is a shape, never a figure.
 *  * **Unsettled is an empty track, never a fake bar.** A row with nothing
 *    recorded against it has no percentage, and drawing a zero-length bar
 *    would say "nothing was spent" where the truth is "nothing has happened
 *    yet". The two are different and the chart must not merge them.
 *
 * The ratio comes from `src/money/plot.ts`, the one quarantined module, and it
 * is unitless. Nothing here turns a figure into text.
 */
import React from "react";
import { View, StyleSheet } from "react-native";

import type { Money } from "@cashkit/api-types";

import { percentOfPlan } from "../../money/plot";
import { color } from "../../ui/tokens";

export function PlanBar({
  actual,
  plan,
  settled,
  testID = "plan-bar",
}: {
  actual: Money | null;
  plan: Money | null;
  /** False when the ledger has recorded nothing for this row in the window. */
  settled: boolean;
  testID?: string;
}) {
  const share = settled ? percentOfPlan(actual, plan) : null;

  return (
    <View testID={testID} style={styles.track} accessibilityRole="progressbar">
      {share ? (
        <View
          testID={`${testID}-fill`}
          style={[
            styles.fill,
            { width: `${share.fillPercent}%` },
            share.overflow && styles.fillOver,
          ]}
        />
      ) : null}
      {/* The plan tick sits at 100% of the track, always. */}
      <View testID={`${testID}-tick`} style={styles.tick} />
    </View>
  );
}

const styles = StyleSheet.create({
  track: {
    height: 6,
    width: "100%",
    borderRadius: 3,
    backgroundColor: color.grid,
    overflow: "hidden",
    justifyContent: "center",
  },
  fill: { height: 6, borderRadius: 3, backgroundColor: color.pine },
  fillOver: { backgroundColor: color.rust },
  tick: { position: "absolute", right: 0, width: 1.5, height: 10, backgroundColor: color.ink },
});
