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

  it("exports nothing that returns a money-shaped object", () => {
    for (const [name, value] of Object.entries(plot)) {
      if (typeof value !== "function") continue;
      expect(name).not.toMatch(/format|display|label|text/i);
    }
  });
});
