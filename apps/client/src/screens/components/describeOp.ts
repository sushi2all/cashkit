/**
 * One proposal operation, in typed, human-readable form.
 *
 * SPEC §5-F2: the card lists "each intent in typed, human-readable form
 * ('Add expense · Rent · 900/month from 2026-03-01')". Typed is the operative
 * word — this is a rendering of the stored operation, field by field, not a
 * summary of it. The user is confirming a change, so what they read has to be
 * what will be applied.
 *
 * Amounts here are the **authored** strings the operation carries — the user's
 * own numbers, echoed back. They are not engine output and they are not
 * reformatted: an authored `900` reads as `900`.
 */
import { shortDate } from "../../ui/provenance";

export interface OperationLine {
  /** `PENDING · <OPERATION>` label text for the card. */
  op: string;
  title: string;
  meta: string;
  amount: string | null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function tagList(tags: unknown): string | null {
  if (!tags || typeof tags !== "object") return null;
  const entries = Object.entries(tags as Record<string, unknown>)
    .filter(([, v]) => typeof v === "string")
    .map(([k, v]) => `${k}:${String(v)}`);
  return entries.length > 0 ? entries.join(" · ") : null;
}

function join(parts: (string | null)[]): string {
  return parts.filter((p): p is string => Boolean(p)).join(" · ");
}

export function describeOperation(operation: Record<string, unknown>): OperationLine {
  const op = str(operation["op"]) ?? "operation";
  const amount = str(operation["amount"]);

  switch (op) {
    case "add_item": {
      const direction = str(operation["direction"]);
      const kind = direction === "in" ? "Add income" : "Add expense";
      return {
        op,
        title: join([kind, str(operation["name"]) ?? str(operation["id"])]),
        meta: join([
          str(operation["recurrence"]),
          str(operation["start"]) ? `from ${shortDate(str(operation["start"]))}` : null,
          str(operation["end"]) ? `to ${shortDate(str(operation["end"]))}` : null,
          str(operation["settlement"]),
          tagList(operation["tags"]),
          str(operation["id"]) ? `item:${str(operation["id"])}` : null,
        ]),
        amount,
      };
    }
    case "set_amount":
      return {
        op,
        title: join(["Change amount", str(operation["item"])]),
        meta: join([
          str(operation["from_date"]) ? `from ${shortDate(str(operation["from_date"]))}` : "from the start",
          str(operation["item"]) ? `item:${str(operation["item"])}` : null,
        ]),
        amount,
      };
    case "shift_items":
      return {
        op,
        title: "Shift dates",
        meta: join([`selector ${str(operation["selector"]) ?? "—"}`, `by ${str(operation["by"]) ?? "—"}`]),
        amount: null,
      };
    case "scale_items":
      return {
        op,
        title: "Scale amounts",
        meta: join([`selector ${str(operation["selector"]) ?? "—"}`, `× ${str(operation["factor"]) ?? "—"}`]),
        amount: null,
      };
    case "add_event":
    case "record_actual": {
      const isActual = op === "record_actual";
      return {
        op,
        title: join([isActual ? "Record actual" : "One-off", str(operation["note"]) ?? str(operation["item"]) ?? "entry"]),
        meta: join([
          isActual ? "ledger · actual" : "one-off · forecast",
          str(operation["date"]) ? shortDate(str(operation["date"])) : null,
          str(operation["item"]) ? `item:${str(operation["item"])}` : null,
        ]),
        amount,
      };
    }
    case "correct_actual":
      return {
        op,
        title: join(["Correct actual", str(operation["event"])]),
        // ADR-0012: the note is mandatory and it is part of the record, so it
        // is shown on the card the user confirms, not tucked away.
        meta: join([`event:${str(operation["event"]) ?? "—"}`, `note: ${str(operation["note"]) ?? ""}`]),
        amount,
      };
    case "fork_scenario":
      return {
        op,
        title: join(["New scenario", str(operation["name"])]),
        meta: join([
          str(operation["parent"]) ? `forked from ${str(operation["parent"])}` : "forked from base",
          str(operation["note"]),
        ]),
        amount: null,
      };
    case "set_cutover":
      return { op, title: "Move the cutover", meta: shortDate(str(operation["date"])), amount: null };
    case "set_horizon":
      return {
        op,
        title: "Change the horizon",
        meta: join([shortDate(str(operation["start"])), shortDate(str(operation["end"]))]),
        amount: null,
      };
    case "set_opening_balance":
      return { op, title: "Set the opening balance", meta: "book parameter", amount };
    case "remove_event":
      return {
        op,
        title: join(["Remove", str(operation["event"])]),
        meta: join([`event:${str(operation["event"]) ?? "—"}`, str(operation["note"])]),
        amount: null,
      };
    case "edit_schedule_date":
      return {
        op,
        title: join(["Schedule date", str(operation["action"])]),
        meta: join([
          str(operation["item"]) ? `item:${str(operation["item"])}` : null,
          shortDate(str(operation["date"])),
          str(operation["new_date"]) ? `→ ${shortDate(str(operation["new_date"]))}` : null,
        ]),
        amount,
      };
    case "save":
      return { op, title: "Save", meta: str(operation["message"]) ?? "", amount: null };
    default:
      // An operation this build does not know how to phrase is still shown.
      // Hiding it would let a change the user never read reach a confirmation.
      return { op, title: op.replace(/_/g, " "), meta: JSON.stringify(operation), amount };
  }
}

/** The `PENDING · <OPERATION>` label of SPEC §6-S4. */
export function proposalLabel(operations: readonly Record<string, unknown>[]): string {
  const first = operations[0];
  const op = first ? (str(first["op"]) ?? "change") : "change";
  const suffix = operations.length > 1 ? ` +${operations.length - 1}` : "";
  return `PENDING · ${op.toUpperCase()}${suffix}`;
}
