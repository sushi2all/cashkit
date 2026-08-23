/**
 * The horizon sparkline on Home, and the chart card on Forecast.
 *
 * Geometry comes from `src/money/plot.ts`, the one module allowed to turn a
 * figure into a coordinate. **Every number rendered as text here is a service
 * string** — the low-point label is `warnings.min_cash.display`, not anything
 * derived from the curve. That is the line between drawing a figure and
 * computing one.
 */
import React from "react";
import { Text, View, StyleSheet } from "react-native";
import Svg, { Circle, Line, Path, Rect } from "react-native-svg";

import type { Money } from "@cashkit/api-types";

import { formatMoney } from "../../money/money";
import { scaleSeries, toAreaPath, toLinePath, toX, toY } from "../../money/plot";
import { color, font } from "../../ui/tokens";
import { monthLabel, shortDate } from "../../ui/provenance";

export function Sparkline({
  closing,
  rangeLabel,
  lowLabel,
  height = 54,
  width = 350,
  testID = "sparkline",
}: {
  closing: readonly (Money | null)[];
  rangeLabel: string;
  lowLabel: string;
  height?: number;
  width?: number;
  testID?: string;
}) {
  const box = { width, height, padTop: 6, padBottom: 6 };
  const scale = scaleSeries(closing);
  const line = toLinePath(scale, box);
  const area = toAreaPath(scale, box);
  const lowPoint = scale.minIndex >= 0 ? scale.points[scale.minIndex] : null;

  return (
    <View testID={testID} style={styles.sparkWrap}>
      <Svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`}>
        {area ? <Path d={area} fill={color.areaFill} /> : null}
        {scale.zero !== null && scale.hasNegative ? (
          <Line
            x1={0}
            x2={width}
            y1={toY(scale.zero, box)}
            y2={toY(scale.zero, box)}
            stroke={color.rust}
            strokeWidth={0.7}
            strokeDasharray="3 3"
          />
        ) : null}
        {line ? <Path d={line} stroke={color.pine} strokeWidth={1.8} fill="none" /> : null}
        {lowPoint != null ? (
          <Circle
            cx={toX(scale.minIndex, scale.points.length, box)}
            cy={toY(lowPoint, box)}
            r={3}
            fill={scale.hasNegative ? color.rust : color.sub}
          />
        ) : null}
      </Svg>
      <View style={styles.sparkLabels}>
        <Text testID={`${testID}-range`} style={styles.sparkLabel}>
          {rangeLabel}
        </Text>
        <Text testID={`${testID}-low`} style={styles.sparkLabel}>
          {lowLabel}
        </Text>
      </View>
    </View>
  );
}

/** The Forecast chart card (SPEC §6-S3): computed area, cutover, min drop. */
export function ForecastChart({
  closing,
  months,
  cutover,
  minCash,
  minCashPeriod,
  height = 192,
  width = 322,
  testID = "forecast-chart",
}: {
  closing: readonly (Money | null)[];
  months: readonly string[];
  cutover: string | null;
  minCash: Money | null;
  minCashPeriod: string | null;
  height?: number;
  width?: number;
  testID?: string;
}) {
  const box = { width, height, padTop: 14, padBottom: 18 };
  const scale = scaleSeries(closing);
  const line = toLinePath(scale, box);
  const area = toAreaPath(scale, box);
  const lowPoint = scale.minIndex >= 0 ? scale.points[scale.minIndex] : null;
  const cutoverIndex = cutover ? months.findIndex((m) => m.slice(0, 7) >= cutover.slice(0, 7)) : -1;
  const cutoverX = cutoverIndex >= 0 ? toX(cutoverIndex, months.length, box) : null;

  return (
    <View testID={testID} style={styles.chartWrap}>
      <Svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`}>
        {cutoverX !== null && cutoverX > 0 ? (
          <Rect x={0} y={0} width={cutoverX} height={height} fill={color.recordedBand} />
        ) : null}
        {area ? <Path d={area} fill={color.areaFill} /> : null}
        {scale.zero !== null && scale.hasNegative ? (
          <Line
            x1={0}
            x2={width}
            y1={toY(scale.zero, box)}
            y2={toY(scale.zero, box)}
            stroke={color.rust}
            strokeWidth={0.8}
          />
        ) : null}
        {line ? <Path d={line} stroke={color.pine} strokeWidth={1.8} fill="none" /> : null}
        {cutoverX !== null ? (
          <Line x1={cutoverX} x2={cutoverX} y1={4} y2={height - 12} stroke={color.ink} strokeWidth={1} />
        ) : null}
        {lowPoint != null ? (
          <Circle
            cx={toX(scale.minIndex, scale.points.length, box)}
            cy={toY(lowPoint, box)}
            r={3.5}
            fill={color.rust}
          />
        ) : null}
      </Svg>
      <View style={styles.chartLabels}>
        <Text testID={`${testID}-recorded`} style={styles.chartLabel}>
          {cutover ? `RECORDED · COMPUTED · CUTOVER ${shortDate(cutover)}` : "ALL COMPUTED"}
        </Text>
        {minCash ? (
          <Text testID={`${testID}-min`} style={[styles.chartLabel, { color: color.rust }]}>
            MIN {formatMoney(minCash)}
            {minCashPeriod ? ` · ${monthLabel(minCashPeriod)}` : ""}
          </Text>
        ) : null}
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
  sparkWrap: { width: "100%" },
  sparkLabels: { flexDirection: "row", justifyContent: "space-between", paddingTop: 4, width: "100%" },
  sparkLabel: { fontFamily: font.mono, fontSize: 9, letterSpacing: 0.7, color: color.faint },
  chartWrap: { width: "100%" },
  chartLabels: { flexDirection: "row", justifyContent: "space-between", width: "100%", paddingTop: 4 },
  chartLabel: { fontFamily: font.mono, fontSize: 8, letterSpacing: 0.6, color: color.sub },
  months: { flexDirection: "row", justifyContent: "space-between", width: "100%", paddingTop: 6 },
  monthTick: { fontFamily: font.mono, fontSize: 8.5, color: color.faint },
});
