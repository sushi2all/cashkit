import { describe, expect, it } from "vitest";

import type { Money } from "@cashkit/api-types";

import { exactOf, formatBare, formatMoney, isNegative, moneyTone } from "../money";

const money = (exact: string, display: string): Money => ({ exact, display });

/** The receipt language groups with a thin space and signs with a real minus. */
const THIN = "\u2009";
const MINUS = "\u2212";

describe("rendering money", () => {
  it("keeps every digit the service sent", () => {
    // The cents are the engine's. A UI that drops them is reporting a figure
    // the engine did not compute (D-MLP-41).
    expect(formatMoney(money("7412.3800", "7412.38"))).toBe(`€7${THIN}412.38`);
    expect(formatMoney(money("0.0100", "0.01"))).toBe("€0.01");
    expect(formatMoney(money("1234567.8900", "1234567.89"))).toBe(`€1${THIN}234${THIN}567.89`);
  });

  it("renders the sign the service sent, as a typographic minus", () => {
    expect(formatMoney(money("-1800.0000", "-1800.00"))).toBe(`${MINUS}€1${THIN}800.00`);
    expect(formatBare(money("-1800.0000", "-1800.00"))).toBe(`${MINUS}1${THIN}800.00`);
  });

  it("groups without changing the digits", () => {
    const cases = ["1.00", "12.00", "123.00", "1234.00", "12345.00", "123456.00", "1234567.00"];
    for (const display of cases) {
      const rendered = formatBare(money(`${display}00`, display));
      // Every digit survives, in order. Grouping inserts separators; it never
      // rounds, reorders or re-derives.
      expect(rendered.replace(/[^0-9.]/g, "")).toBe(display);
    }
  });

  it("shows absent as a dash rather than as zero", () => {
    // The engine distinguishes absent from zero and so does the screen.
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney(undefined)).toBe("—");
    expect(formatMoney(money("0.0000", "0.00"))).toBe("€0.00");
  });

  it("detects a negative figure by its leading character, not by comparison", () => {
    expect(isNegative(money("-0.0100", "-0.01"))).toBe(true);
    expect(isNegative(money("0.0000", "0.00"))).toBe(false);
    expect(isNegative(null)).toBe(false);
    expect(moneyTone(money("-5.0000", "-5.00"))).toBe("rust");
    expect(moneyTone(money("5.0000", "5.00"))).toBe("ink");
  });

  it("exposes the lossless value for the one place it belongs", () => {
    expect(exactOf(money("2940.1234", "2940.12"))).toBe("2940.1234");
  });
});
