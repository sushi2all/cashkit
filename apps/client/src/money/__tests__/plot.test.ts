import { describe, expect, it } from "vitest";

import type { Money } from "@cashkit/api-types";

import * as plot from "../plot";

const money = (n: string): Money => ({ exact: `${n}.0000`, display: `${n}.00` });

describe("the quarantined plot module", () => {
  it("returns positions, never money", () => {
    // The guarantee that makes the lint exception safe: nothing this module
    // exports can be rendered as a figure, because nothing returns a string
    // that came from a money value.
    const scale = plot.scaleSeries([money("100"), money("200"), money("50")]);
    for (const point of scale.points) {
      expect(typeof point).toBe("number");
      expect(point).toBeGreaterThanOrEqual(0);
      expect(point).toBeLessThanOrEqual(1);
    }
  });

  it("finds the lowest and highest points by index", () => {
    const scale = plot.scaleSeries([money("100"), money("200"), money("50")]);
    expect(scale.minIndex).toBe(2);
    expect(scale.maxIndex).toBe(1);
  });

  it("keeps zero in range and reports a crossing", () => {
    const scale = plot.scaleSeries([money("100"), money("-50")]);
    expect(scale.hasNegative).toBe(true);
    expect(scale.zero).not.toBeNull();
  });

  it("keeps absent apart from zero", () => {
    const scale = plot.scaleSeries([money("100"), null, money("50")]);
    expect(scale.points[1]).toBeNull();
    expect(scale.points[0]).not.toBeNull();
  });

  it("survives an empty and a flat series without collapsing", () => {
    expect(plot.scaleSeries([]).points).toEqual([]);
    const flat = plot.scaleSeries([money("100"), money("100")]);
    for (const point of flat.points) expect(Number.isFinite(point)).toBe(true);
  });

  it("emits path data made only of coordinates", () => {
    const scale = plot.scaleSeries([money("100"), money("200")]);
    const d = plot.toLinePath(scale, { width: 100, height: 50 });
    // Path data is commands and coordinates and nothing else: no currency
    // symbol, no sign, no separator, nothing a reader could mistake for a
    // figure. (A coordinate may coincidentally read like one — that is why the
    // guarantee is about the character set, not about the digits.)
    expect(d).toMatch(/^M[\d. ]+ L[\d. ]+$/);
    expect(d).not.toMatch(/[€\u2212\u2009]/);
  });

  describe("the y-axis reference", () => {
    it("names its marks by index, so the axis text is the service's own", () => {
      const scale = plot.scaleSeries([money("100"), money("200"), money("50")]);
      const ticks = plot.axisTicks(scale);
      const high = ticks.find((t) => t.kind === "high");
      const low = ticks.find((t) => t.kind === "low");
      expect(high?.index).toBe(1);
      expect(low?.index).toBe(2);
      // Nothing here carries text: a label is read from the payload by index.
      for (const tick of ticks) expect(typeof tick.ratio).toBe("number");
    });

    it("reads top to bottom", () => {
      const ticks = plot.axisTicks(plot.scaleSeries([money("100"), money("-50")]));
      const ratios = ticks.map((t) => t.ratio);
      expect([...ratios].sort((a, b) => b - a)).toEqual(ratios);
    });

    it("marks zero only when the series crosses it", () => {
      const crossing = plot.axisTicks(plot.scaleSeries([money("100"), money("-50")]));
      expect(crossing.some((t) => t.kind === "zero")).toBe(true);
      expect(crossing.find((t) => t.kind === "zero")?.index).toBeNull();
      const positive = plot.axisTicks(plot.scaleSeries([money("100"), money("200")]));
      expect(positive.some((t) => t.kind === "zero")).toBe(false);
    });

    it("keeps a small negative low when it would collide with the zero line", () => {
      const ticks = plot.axisTicks(plot.scaleSeries([money("10000"), money("-1")]));
      expect(ticks.some((t) => t.kind === "low")).toBe(true);
      expect(ticks.some((t) => t.kind === "zero")).toBe(false);
    });

    it("drops a mark that would sit on the one above it", () => {
      // A flat series has one figure worth naming, not two stacked on a line.
      const ticks = plot.axisTicks(plot.scaleSeries([money("100"), money("100")]));
      expect(ticks).toHaveLength(1);
      expect(ticks[0]?.kind).toBe("high");
    });

    it("says nothing about an empty series", () => {
      expect(plot.axisTicks(plot.scaleSeries([]))).toEqual([]);
    });
  });

  describe("the axis gutter", () => {
    it("starts the curve clear of the labels", () => {
      const box = { width: 100, height: 50, padLeft: 40 };
      expect(plot.toX(0, 3, box)).toBe(40);
      expect(plot.toX(2, 3, box)).toBe(100);
      expect(plot.toX(0, 1, box)).toBe(70);
    });

    it("is absent by default, so a chart without an axis is unchanged", () => {
      expect(plot.toX(0, 3, { width: 100, height: 50 })).toBe(0);
    });
  });

  describe("scaleTogether", () => {
    it("puts every series on one range, so two curves are comparable", () => {
      const { scales } = plot.scaleTogether([
        [money("100"), money("200")],
        [money("0"), money("400")],
      ]);
      // The second series reaches the top of the shared box; the first does
      // not. Scaled separately both would have touched it, which is the
      // misreading a comparison chart must not produce.
      expect(scales[1]!.points[1]).toBe(1);
      expect(scales[0]!.points[1]).toBeLessThan(1);
    });

    it("shares one zero line and one crossing verdict", () => {
      const { scales, zero, hasNegative } = plot.scaleTogether([
        [money("100")],
        [money("-50")],
      ]);
      expect(hasNegative).toBe(true);
      expect(zero).not.toBeNull();
      expect(scales[0]!.zero).toBe(zero);
      expect(scales[1]!.zero).toBe(zero);
    });

    it("keeps an absent figure absent", () => {
      const { scales } = plot.scaleTogether([[money("100"), null]]);
      expect(scales[0]!.points[1]).toBeNull();
    });
  });

  describe("percentOfPlan", () => {
    it("is a ratio of magnitudes, so income and expense read alike", () => {
      expect(plot.percentOfPlan(money("-50"), money("-100"))!.fill).toBeCloseTo(0.5);
      expect(plot.percentOfPlan(money("50"), money("100"))!.fill).toBeCloseTo(0.5);
    });

    it("clamps the fill at the track and reports the overflow separately", () => {
      const over = plot.percentOfPlan(money("-150"), money("-100"))!;
      expect(over.fill).toBe(1);
      expect(over.overflow).toBe(true);
    });

    it("has no bar without a plan to be a percentage of", () => {
      // SPEC §6-S8: an unsettled or unplanned row shows an empty track, never
      // a fake bar. A missing denominator must not become a drawn one.
      expect(plot.percentOfPlan(money("-50"), null)).toBeNull();
      expect(plot.percentOfPlan(money("-50"), money("0"))).toBeNull();
      expect(plot.percentOfPlan(null, money("-100"))).toBeNull();
    });
  });

  it("exports nothing that returns a money-shaped object", () => {
    for (const [name, value] of Object.entries(plot)) {
      if (typeof value !== "function") continue;
      expect(name).not.toMatch(/format|display|label|text/i);
    }
  });
});
