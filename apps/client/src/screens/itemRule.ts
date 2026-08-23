/**
 * An item's rule and segments, assembled from the engine's own trace output.
 *
 * ## Why this exists
 *
 * SPEC §6-S9 asks the Item screen for a rule card (amount, repeats, starts,
 * ends, escalation) and a segments card (one row per segment, with its window).
 * **No endpoint exposes an item's authored configuration.** `GET /book/state`
 * carries an item's identity and its computed columns — id, name, kind,
 * direction, tags, formula, cash, accrual — and nothing about the `Segment`
 * list, the `Recurrence`, the `Escalation` or the `Amount.schedule` that
 * produced them. Escalated as D-MLP-66; this session does not change the
 * service.
 *
 * ## What is done instead
 *
 * `GET /book/trace` answers, for one item in one period, with the engine's own
 * account of how the figure was made: which segment it came from
 * (`binding.source` — "segment starting 2026-03-01"), how that segment repeats
 * (`binding.detail` — "recurrence every 1 month, anchor period_start"), what
 * escalation was applied (`binding.source` on the escalation binding — "rate
 * 0.02 compounded 0 time(s)"), and the arithmetic (`step.expression` —
 * "segments[1].amount x (1 + 0.02)^0"). Tracing each period the item is
 * non-zero in and grouping the results by segment reconstructs the rule out of
 * statements the engine made.
 *
 * ## What it therefore cannot claim
 *
 * Two things, and both are stated on the screen rather than glossed:
 *
 *  * **Only segments active inside the horizon are visible.** A segment that
 *    ended before the horizon started produced no period and left no trace.
 *  * **A segment's end is inferred from where the next one begins**, and an
 *    open-ended last segment cannot be told apart from one that ends at the
 *    horizon.
 *
 * Every figure here is a `Money` the engine produced, passed through untouched.
 * Every phrase is an engine string quoted whole. Nothing is computed.
 */
import type { Money, Trace, TraceResponse } from "@cashkit/api-types";

export interface SegmentView {
  /** The engine's own words for where this segment starts. */
  source: string;
  /** The engine's own words for how it repeats. */
  recurrence: string | null;
  /** The escalation the engine applied, in its own words, or null. */
  escalation: string | null;
  /** The periods inside the horizon this segment produced, in order. */
  periods: { period: string; amount: Money; contribution: Money }[];
  /** The arithmetic the engine showed for the most recent of them. */
  steps: { expression: string; operation: string; value: Money; rounding: string }[];
}

export interface ItemRule {
  segments: SegmentView[];
  /** True when the engine showed an escalation step anywhere in the horizon. */
  escalates: boolean;
}

function binding(trace: Trace, kind: string) {
  return trace.bindings.find((b) => b.kind === kind) ?? null;
}

/**
 * Group the traced periods by the segment the engine says they came from.
 *
 * Traces must arrive in period order; the grouping preserves it, and a segment
 * that reappears after another one is kept as a separate group rather than
 * merged, because the engine saying so twice is a fact about the engine.
 */
export function assembleRule(traces: readonly TraceResponse[]): ItemRule {
  const segments: SegmentView[] = [];
  let escalates = false;

  for (const response of traces) {
    const trace = response.trace;
    const segment = binding(trace, "segment");
    const escalation = binding(trace, "escalation");
    if (escalation) escalates = true;

    const source = segment?.source ?? "no segment reported";
    const last = segments[segments.length - 1];
    const entry =
      last && last.source === source
        ? last
        : (() => {
            const created: SegmentView = {
              source,
              recurrence: segment?.detail ?? null,
              escalation: escalation?.source ?? null,
              periods: [],
              steps: [],
            };
            segments.push(created);
            return created;
          })();

    if (segment) {
      entry.periods.push({
        period: trace.period_start,
        amount: segment.value,
        contribution: trace.value,
      });
    }
    if (escalation && !entry.escalation) entry.escalation = escalation.source;
    entry.steps = trace.steps.map((step) => ({
      expression: step.expression,
      operation: step.operation,
      value: step.value,
      rounding: step.rounding,
    }));
  }

  return { segments, escalates };
}

/** The window a segment covers inside the horizon, as the engine reported it. */
export function segmentWindow(segment: SegmentView): { first: string; last: string } | null {
  const first = segment.periods[0];
  const last = segment.periods[segment.periods.length - 1];
  if (!first || !last) return null;
  return { first: first.period, last: last.period };
}
