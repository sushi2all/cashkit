/**
 * The compare chart of SPEC §6-S6: one curve per scenario on one shared scale.
 *
 * Geometry comes from `src/money/plot.ts`, the one quarantined module. Every
 * number rendered as text here is a service string — the diverge label carries
 * the service's own `delta`, not a difference worked out from the curve.
 *
 * The zero line and the negative region are drawn whenever any plotted figure
 * is below zero. A comparison chart that hid its own zero line would flatter
 * the fork that goes negative, which is the one thing this screen exists to
 * show.
 */
import React from "react";
import { Text, View, StyleSheet } from "react-native";
import Svg, { Circle, Line, Path, Rect } from "react-native-svg";

import type { Money } from "@cashkit/api-types";

import { formatMoney } from "../../money/money";
import { bandBelow, scaleTogether, toLinePath, toX, toY } from "../../money/plot";
import { monthLabel } from "../../ui/provenance";
import { color, font } from "../../ui/tokens";

export interface CompareSeries {
  scenario: string;
  values: (Money | null)[];
}

export function CompareChart({
  months,
  series,
  divergePeriod,
  divergeDelta,
  height = 190,
  width = 322,
  testID = "compare-chart",
}: {
  months: readonly string[];
  series: readonly CompareSeries[];
  /** The first period where the scenarios stop agreeing, or null. */
  divergePeriod: string | null;
  /** The service's own delta at that period. Never derived here. */
  divergeDelta: Money | null;
  height?: number;
  width?: number;
  testID?: string;
}) {
  const box = { width, height, padTop: 14, padBottom: 18 };
  const { scales, zero, hasNegative } = scaleTogether(series.map((s) => s.values));
  const strokes = [color.ink, color.pine, color.rust, color.sub];
  const divergeIndex = divergePeriod
    ? months.findIndex((m) => m.slice(0, 7) === divergePeriod.slice(0, 7))
    : -1;
  const zeroY = zero === null ? null : toY(zero, box);
  const negativeBand = zero === null ? null : bandBelow(zero, box);

  return (
    <View testID={testID} style={styles.wrap}>
      <View style={styles.legend}>
        {series.map((one, index) => (
          <View key={one.scenario} style={styles.legendItem}>
            <View style={[styles.swatch, { backgroundColor: strokes[index % strokes.length] }]} />
            <Text testID={`${testID}-legend-${one.scenario}`} style={styles.legendLabel}>
              {one.scenario.toUpperCase()}
            </Text>
          </View>
        ))}
      </View>

      <Svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`}>
        {negativeBand !== null && hasNegative ? (
          <Rect
            x={0}
            y={negativeBand.y}
            width={width}
            height={negativeBand.height}
            fill={color.areaFill}
          />
        ) : null}
        {zeroY !== null && hasNegative ? (
          <Line x1={0} x2={width} y1={zeroY} y2={zeroY} stroke={color.rust} strokeWidth={0.8} />
        ) : null}
        {scales.map((scale, index) => {
          const d = toLinePath(scale, box);
          if (!d) return null;
          return (
            <Path
              key={series[index]?.scenario ?? index}
              d={d}
              stroke={strokes[index % strokes.length]}
              strokeWidth={1.8}
              fill="none"
              strokeDasharray={index === 0 ? undefined : "4 3"}
            />
          );
        })}
        {divergeIndex >= 0
          ? scales.map((scale, index) => {
              const point = scale.points[divergeIndex];
              if (point == null) return null;
              return (
                <Circle
                  key={`diverge-${index}`}
                  cx={toX(divergeIndex, months.length, box)}
                  cy={toY(point, box)}
                  r={3}
                  fill={strokes[index % strokes.length]}
                />
              );
            })
          : null}
      </Svg>

      <View style={styles.labels}>
        {divergePeriod ? (
          <Text testID={`${testID}-diverge`} style={styles.diverge}>
            {`DIVERGE ${monthLabel(divergePeriod)}${
              divergeDelta ? ` · Δ ${formatMoney(divergeDelta)}` : ""
            }`}
          </Text>
        ) : (
          <Text testID={`${testID}-identical`} style={styles.label}>
            THESE SCENARIOS AGREE IN EVERY PERIOD
          </Text>
        )}
      </View>

      <View style={styles.months}>
        {months.map((month) => (
          <Text key={month} style={styles.monthTick}>
            {monthLabel(month).slice(0, 3)}
          </Text>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { width: "100%" },
  legend: { flexDirection: "row", gap: 14, paddingBottom: 6 },
  legendItem: { flexDirection: "row", alignItems: "center", gap: 5 },
  swatch: { width: 10, height: 2 },
  legendLabel: { fontFamily: font.mono, fontSize: 8.5, letterSpacing: 0.7, color: color.sub },
  labels: { flexDirection: "row", justifyContent: "space-between", width: "100%", paddingTop: 4 },
  label: { fontFamily: font.mono, fontSize: 8, letterSpacing: 0.6, color: color.sub },
  diverge: { fontFamily: font.mono, fontSize: 8.5, letterSpacing: 0.6, color: color.rust },
  months: { flexDirection: "row", justifyContent: "space-between", width: "100%", paddingTop: 6 },
  monthTick: { fontFamily: font.mono, fontSize: 8.5, color: color.faint },
});
