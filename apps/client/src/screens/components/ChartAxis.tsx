/**
 * The y-axis reference every curve in this app is read against.
 *
 * A balance curve with no scale is a shape, not a figure: two charts of the
 * same book can look identical and mean thousands apart. So each chart carries
 * a gutter of marks — and the marks are **the series' own figures**, the high
 * and the low, printed from the service's `display` string and named by index
 * (`money/plot.ts` returns the index, never the text). A conventional axis of
 * round numbers would put figures on the screen the engine never computed,
 * which is the one thing this client does not do.
 *
 * The zero line is the exception, and it carries a word rather than a figure
 * for the same reason: "0" would be a money string of the client's own making.
 */
import React from "react";
import { Line, Text as SvgText } from "react-native-svg";

import type { Money } from "@cashkit/api-types";

import { formatMoney } from "../../money/money";
import { axisTicks, toY, type Box, type PlotScale } from "../../money/plot";
import { color, font } from "../../ui/tokens";

/** The narrowest gutter, used when a chart has no labels to print. */
export const AXIS_GUTTER = 28;

/**
 * The gutter the labels sit in, sized from the labels themselves. Charts pass
 * it as `padLeft` so curves clear it. A fixed width either wastes a small
 * sparkline or lets `−€12 345.67` run into the curve.
 */
export function axisGutter(scale: PlotScale, figures: readonly (Money | null)[], fontSize = 8): number {
  const longest = Math.max(
    0,
    ...axisTicks(scale).map((tick) =>
      tick.kind === "zero" ? 4 : formatMoney(tick.index === null ? null : figures[tick.index]).length,
    ),
  );
  // ponytail: mono glyphs are ~0.6em wide; measure with onLayout if a font change breaks this.
  return Math.max(AXIS_GUTTER, Math.ceil(longest * fontSize * 0.6) + 6);
}

export function ChartAxis({
  scale,
  figures,
  box,
  fontSize = 8,
  testID = "chart-axis",
}: {
  scale: PlotScale;
  /**
   * The plotted series, in plotted order. The axis reads its labels out of
   * this — it never formats a figure of its own.
   */
  figures: readonly (Money | null)[];
  box: Box;
  fontSize?: number;
  testID?: string;
}) {
  return (
    <>
      {axisTicks(scale).map((tick) => {
        const y = toY(tick.ratio, box);
        const zero = tick.kind === "zero";
        return (
          <React.Fragment key={tick.kind}>
            <Line
              x1={box.padLeft ?? 0}
              x2={box.width}
              y1={y}
              y2={y}
              stroke={zero ? color.rust : color.grid}
              strokeWidth={zero ? 0.8 : 0.7}
            />
            <SvgText
              testID={`${testID}-${tick.kind}`}
              x={0}
              y={y + fontSize / 2 - 1}
              fontFamily={font.mono}
              fontSize={fontSize}
              fill={zero ? color.rust : color.faint}
            >
              {zero ? "ZERO" : formatMoney(tick.index === null ? null : figures[tick.index])}
            </SvgText>
          </React.Fragment>
        );
      })}
    </>
  );
}
