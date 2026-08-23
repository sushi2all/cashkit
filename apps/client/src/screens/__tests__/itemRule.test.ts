import { describe, expect, it } from "vitest";

import type { Money, TraceResponse } from "@cashkit/api-types";

import { assembleRule, segmentWindow } from "../itemRule";

const money = (n: string): Money => ({ exact: `${n}.0000`, display: `${n}.00` });

function trace(options: {
  period: string;
  value: string;
  segmentSource: string;
  segmentAmount: string;
  detail?: string;
  escalation?: string;
  steps?: { expression: string; value: string }[];
}): TraceResponse {
  return {
    as_of: "2026-03-17",
    scenario: "base",
    revision: "abc1234",
    engine_version: "1",
    what_if: { stamped: false, reason: null, scenario: null },
    request_id: "r",
    period: options.period,
    measure: "cash",
    trace: {
      item_id: "rent",
      item_name: "Rent",
      kind: "generated",
      measure: "cash",
      period_index: 0,
      period_start: options.period,
      period_end: options.period,
      value: money(options.value),
      formula: "segments[0] contribution to cash",
      bindings: [
        {
          symbol: "segments[0].amount",
          kind: "segment",
          value: money(options.segmentAmount),
          source: options.segmentSource,
          target: "rent",
          detail: options.detail ?? "recurrence every 1 month, anchor period_start",
        },
        ...(options.escalation
          ? [
              {
                symbol: "segments[0].escalation",
                kind: "escalation",
                value: money("1"),
                source: options.escalation,
                target: "",
                detail: "(1 + r)^n computed in Decimal",
              },
            ]
          : []),
      ],
      steps: (options.steps ?? []).map((step) => ({
        expression: step.expression,
        operation: "base amount",
        inputs: [options.period],
        value: money(step.value),
        rounding: "none",
      })),
      children: [],
      depth: 0,
      truncated: false,
      reconciles: true,
      notes: [],
      diagnostics: [],
    },
  } as unknown as TraceResponse;
}

describe("assembling an item's rule from the engine's own traces", () => {
  it("groups consecutive periods under the segment the engine named", () => {
    const rule = assembleRule([
      trace({ period: "2026-01-01", value: "-1150", segmentSource: "segment starting 2025-01-01", segmentAmount: "-1150" }),
      trace({ period: "2026-02-01", value: "-1150", segmentSource: "segment starting 2025-01-01", segmentAmount: "-1150" }),
      trace({ period: "2026-03-01", value: "-1200", segmentSource: "segment starting 2026-03-01", segmentAmount: "-1200" }),
    ]);

    expect(rule.segments).toHaveLength(2);
    expect(rule.segments[0]!.source).toBe("segment starting 2025-01-01");
    expect(rule.segments[0]!.periods).toHaveLength(2);
    expect(rule.segments[1]!.source).toBe("segment starting 2026-03-01");
  });

  it("keeps the engine's phrases whole rather than parsing them", () => {
    // The recurrence and the escalation are quoted, not interpreted. A client
    // that parsed "every 1 month" into a number would be re-deriving a rule the
    // engine already stated.
    const rule = assembleRule([
      trace({
        period: "2026-04-01",
        value: "-1200",
        segmentSource: "segment starting 2026-03-01",
        segmentAmount: "-1200",
        detail: "recurrence every 3 months, anchor day_of_month; occurrence on 2026-04-01",
        escalation: "rate 0.02 compounded 0 time(s), anchor segment_start",
      }),
    ]);
    expect(rule.segments[0]!.recurrence).toBe(
      "recurrence every 3 months, anchor day_of_month; occurrence on 2026-04-01",
    );
    expect(rule.segments[0]!.escalation).toBe("rate 0.02 compounded 0 time(s), anchor segment_start");
    expect(rule.escalates).toBe(true);
  });

  it("passes every figure through untouched", () => {
    const rule = assembleRule([
      trace({ period: "2026-01-01", value: "-1150", segmentSource: "s", segmentAmount: "-1150" }),
    ]);
    expect(rule.segments[0]!.periods[0]!.amount).toEqual(money("-1150"));
    expect(rule.segments[0]!.periods[0]!.contribution).toEqual(money("-1150"));
  });

  it("reports the window it can actually see", () => {
    const rule = assembleRule([
      trace({ period: "2026-01-01", value: "-1150", segmentSource: "s", segmentAmount: "-1150" }),
      trace({ period: "2026-05-01", value: "-1150", segmentSource: "s", segmentAmount: "-1150" }),
    ]);
    expect(segmentWindow(rule.segments[0]!)).toEqual({ first: "2026-01-01", last: "2026-05-01" });
  });

  it("says nothing at all when the engine traced nothing", () => {
    const rule = assembleRule([]);
    expect(rule.segments).toEqual([]);
    expect(rule.escalates).toBe(false);
    expect(segmentWindow({ source: "s", recurrence: null, escalation: null, periods: [], steps: [] })).toBeNull();
  });
});
