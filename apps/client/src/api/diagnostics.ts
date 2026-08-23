/**
 * Reading a diagnostic list off a payload that declares it untyped.
 *
 * Four service payloads — `BookState`, `Forecast`, `CompareResponse` and
 * `ValidateResponse` — declare `diagnostics: list` rather than
 * `list[DiagnosticOut]`, so the generated client types them `unknown[]` while
 * the wire shape is the ordinary diagnostic. `ReconciliationOut` and
 * `ProposalOut` declare theirs properly, which is how we know the loose ones
 * are an oversight rather than a different shape. Escalated as D-MLP-56; the
 * fix belongs to the service, not here.
 *
 * **This module reshapes nothing.** It copies the six fields `DiagnosticOut`
 * declares, string for string, and returns them. It never rewrites a message,
 * never shortens a suggested fix, never drops an entry it does not recognize
 * and never invents a field the service did not send (ADR-0015).
 *
 * Two mechanical guards stand behind that claim:
 *
 *  1. `EXHAUSTIVE` below fails to compile the moment `DiagnosticOut` gains a
 *     field, so a new field cannot be silently left on the floor.
 *  2. `e2e/web/diagnostics.spec.ts` compares the rendered text against the raw
 *     JSON of `GET /book/validate`, field by field, so anything this function
 *     lost would fail the gate in a browser.
 */
import type { Diagnostic } from "@cashkit/api-types";

/**
 * Every field `DiagnosticOut` declares. A new one breaks this line, which is
 * the point: the narrowing below has to learn about it before the build passes.
 */
const EXHAUSTIVE: Record<keyof Diagnostic, true> = {
  code: true,
  severity: true,
  message: true,
  suggested_fix: true,
  item_id: true,
  field: true,
};

export const DIAGNOSTIC_FIELDS = Object.keys(EXHAUSTIVE) as (keyof Diagnostic)[];

function text(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value : "";
}

function optional(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" ? value : null;
}

/**
 * The diagnostics on a loosely typed payload, verbatim.
 *
 * An entry that is not an object at all is still surfaced — as a diagnostic
 * whose message is the entry itself — because a payload the client cannot read
 * is exactly the thing a user must not have hidden from them.
 */
export function asDiagnostics(raw: readonly unknown[] | null | undefined): Diagnostic[] {
  if (!raw) return [];
  return raw.map((entry): Diagnostic => {
    if (!entry || typeof entry !== "object") {
      return {
        code: "",
        severity: "info",
        message: String(entry),
        suggested_fix: "",
        item_id: null,
        field: null,
      };
    }
    const record = entry as Record<string, unknown>;
    return {
      code: text(record, "code"),
      severity: text(record, "severity"),
      message: text(record, "message"),
      suggested_fix: text(record, "suggested_fix"),
      item_id: optional(record, "item_id"),
      field: optional(record, "field"),
    };
  });
}
