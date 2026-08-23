/**
 * Chart geometry — the one module allowed to turn a money figure into a number.
 *
 * ## Why an exception exists at all
 *
 * The Home sparkline and the Forecast chart have to draw a balance curve, and
 * a curve is pixels. There is no way to place a point on a line without
 * converting the figure it represents into a coordinate. The service does not
 * ship geometry and S3 does not change the service, so the conversion happens
 * here or the screens do not exist.
 *
 * ## Why it is safe
 *
 * The exception is made narrow by construction rather than by promise:
 *
 *  1. **One file.** `cashkit/no-money-arithmetic` names this path in its
 *     `allowIn` option. Every other file in the app — every screen, every
 *     component, every helper — is still forbidden from converting. Widening
 *     the allowance means editing `eslint.config.mjs`, which is a visible diff
 *     in review, not a stray call site.
 *  2. **The output is unitless.** Everything here returns `PlotRatio`, a
 *     branded number in 0…1 describing *where in the drawing box* a point
 *     sits. A ratio is not a euro amount and cannot be mistaken for one: it
 *     has lost the scale, the sign convention and the currency.
 *  3. **The output is never text.** No function here returns a money string,
 *     and `plot.test.ts` asserts it. Every figure the user reads on a chart —
 *     the low point, the axis callouts — is rendered from the service's own
 *     `display` string by the calling screen, not from anything computed here.
 *
 * So the invariant the product actually depends on is intact: no number the
 * user reads as money was computed in the client. What was computed here is
 * the position of a dot.
 */
import type { Money } from "@cashkit/api-types";

/** A position inside the drawing box, 0 = bottom, 1 = top. Never a money value. */
export type PlotRatio = number & { readonly __brand: "PlotRatio" };

export interface PlotScale {
  /** One ratio per input figure, in input order. Absent figures stay absent. */
  points: (PlotRatio | null)[];
  /** Index of the lowest figure, or -1 when the series is empty. */
  minIndex: number;
  /** Index of the highest figure, or -1 when the series is empty. */
  maxIndex: number;
  /** Where the zero line sits, or null when zero is outside the plotted range. */
  zero: PlotRatio | null;
  /** True when at least one plotted figure is below zero. */
  hasNegative: boolean;
}

const ratio = (n: number): PlotRatio => n as PlotRatio;

function magnitude(value: Money): number {
  // The single conversion this module exists for. `exact` rather than
  // `display`, so the dot sits where the engine's own figure is.
  return Number(value.exact);
}

/**
 * Map a series of figures onto the drawing box.
 *
 * The range is padded so a flat series does not collapse to a single line, and
 * zero is included in the range whenever the series crosses it — a chart that
 * hides its own zero line misrepresents a negative month.
 */
export function scaleSeries(values: readonly (Money | null | undefined)[]): PlotScale {
  const numbers: (number | null)[] = values.map((v) => (v ? magnitude(v) : null));
  const present = numbers.filter((n): n is number => n !== null);

  if (present.length === 0) {
    return { points: numbers.map(() => null), minIndex: -1, maxIndex: -1, zero: null, hasNegative: false };
  }

  let lo = Math.min(...present);
  let hi = Math.max(...present);
  const hasNegative = lo < 0;
  if (lo > 0) lo = 0;
  if (hi < 0) hi = 0;
  if (hi === lo) {
    hi = hi + 1;
    lo = lo - 1;
  }
  const span = hi - lo;

  let minIndex = -1;
  let maxIndex = -1;
  numbers.forEach((n, i) => {
    if (n === null) return;
    if (minIndex === -1 || n < (numbers[minIndex] as number)) minIndex = i;
    if (maxIndex === -1 || n > (numbers[maxIndex] as number)) maxIndex = i;
  });

  return {
    points: numbers.map((n) => (n === null ? null : ratio((n - lo) / span))),
    minIndex,
    maxIndex,
    zero: lo <= 0 && hi >= 0 ? ratio((0 - lo) / span) : null,
    hasNegative,
  };
}

export interface Box {
  width: number;
  height: number;
  padTop?: number;
  padBottom?: number;
}

/** Turn a ratio into a y coordinate inside a drawing box (SVG y grows down). */
export function toY(r: PlotRatio, box: Box): number {
  const top = box.padTop ?? 0;
  const bottom = box.padBottom ?? 0;
  const usable = box.height - top - bottom;
  return top + (1 - r) * usable;
}

/** Turn an index into an x coordinate inside a drawing box. */
export function toX(index: number, count: number, box: Box): number {
  if (count <= 1) return box.width / 2;
  return (index / (count - 1)) * box.width;
}

const round = (n: number): string => n.toFixed(2);

/** An SVG polyline through the plotted points. Coordinates only — no money. */
export function toLinePath(scale: PlotScale, box: Box): string {
  const count = scale.points.length;
  const segments: string[] = [];
  scale.points.forEach((p, i) => {
    if (p === null) return;
    const cmd = segments.length === 0 ? "M" : "L";
    segments.push(`${cmd}${round(toX(i, count, box))} ${round(toY(p, box))}`);
  });
  return segments.join(" ");
}

/** The same shape closed to the baseline, for the tinted area under the curve. */
export function toAreaPath(scale: PlotScale, box: Box): string {
  const line = toLinePath(scale, box);
  if (!line) return "";
  const count = scale.points.length;
  const first = scale.points.findIndex((p) => p !== null);
  let last = -1;
  scale.points.forEach((p, i) => {
    if (p !== null) last = i;
  });
  if (first === -1 || last === -1) return "";
  const baseline = scale.zero === null ? box.height - (box.padBottom ?? 0) : toY(scale.zero, box);
  return (
    `${line} L${round(toX(last, count, box))} ${round(baseline)}` +
    ` L${round(toX(first, count, box))} ${round(baseline)} Z`
  );
}

/**
 * One shared scale across several series, so two curves on the same chart can
 * be read against each other (SPEC §6-S6, the compare chart).
 *
 * Scaling each series on its own would draw two lines that look alike and mean
 * different things — the exact misreading a comparison view exists to prevent.
 * So the range is taken over every figure in every series at once, and each
 * series is then mapped onto it.
 */
export function scaleTogether(
  series: readonly (readonly (Money | null | undefined)[])[],
): { scales: PlotScale[]; zero: PlotRatio | null; hasNegative: boolean } {
  const flat = series.flat();
  const combined = scaleSeries(flat);
  let cursor = 0;
  const scales: PlotScale[] = series.map((one) => {
    const points = combined.points.slice(cursor, cursor + one.length);
    cursor += one.length;
    let minIndex = -1;
    let maxIndex = -1;
    points.forEach((p, i) => {
      if (p === null) return;
      if (minIndex === -1 || p < (points[minIndex] as number)) minIndex = i;
      if (maxIndex === -1 || p > (points[maxIndex] as number)) maxIndex = i;
    });
    return {
      points,
      minIndex,
      maxIndex,
      zero: combined.zero,
      hasNegative: combined.hasNegative,
    };
  });
  return { scales, zero: combined.zero, hasNegative: combined.hasNegative };
}

/**
 * How full the Plan-vs-Actual bar is (SPEC §5-F5, §6-S8).
 *
 * The bar encodes **percent of plan** with a tick at 100%, and both figures are
 * the engine's. The magnitudes are compared rather than the signed values, so
 * an expense and an income read the same way: a bar that is longer than the
 * tick means more than planned, in whichever direction the line runs.
 *
 * Returns `null` when there is no plan to be a percentage of, and the caller
 * draws an empty track — a bar with no denominator would be a fake bar, and
 * SPEC §6-S8 forbids exactly that.
 *
 * `overflow` is true when the actual exceeds the plan, so the caller can draw
 * the part past the tick differently without needing the raw ratio.
 */
export function percentOfPlan(
  actual: Money | null | undefined,
  plan: Money | null | undefined,
): { fill: PlotRatio; overflow: boolean } | null {
  if (!actual || !plan) return null;
  const planned = Math.abs(Number(plan.exact));
  if (planned === 0) return null;
  const got = Math.abs(Number(actual.exact));
  const share = got / planned;
  return { fill: ratio(Math.min(share, 1)), overflow: share > 1 };
}

/** Where the 100% tick sits inside a bar drawn at `PLAN_TICK` of its track. */
export const PLAN_TICK: PlotRatio = 1 as PlotRatio;

/**
 * The band between a ratio and the bottom of the box — the shaded negative
 * region under a zero line (SPEC §6-S6).
 *
 * It lives here rather than in the chart component because clamping a
 * coordinate is still arithmetic, and the money rule bans arithmetic outside
 * this module on purpose: a component that may do `Math.max` on a coordinate
 * is a component that may do `Math.max` on a figure.
 */
export function bandBelow(r: PlotRatio, box: Box): { y: number; height: number } {
  const y = toY(r, box);
  const bottom = box.height - (box.padBottom ?? 0);
  return { y, height: Math.max(bottom - y, 0) };
}
