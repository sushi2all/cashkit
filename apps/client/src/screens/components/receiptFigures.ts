/**
 * Pull the quotable figures out of a read receipt.
 *
 * A receipt is one executed read intent and what the engine answered
 * (`{op, scenario, request, payload}`). The answer card shows those figures as
 * leader-dot rows. Everything here is **selection**, never computation: a row
 * is a money object the service put in the payload, moved onto the screen with
 * its label. No total is added up, nothing is compared, nothing is derived.
 *
 * The reply sentence is the model's, and it quotes these same engine numbers
 * (ADR-0030 stage 3). The rows are what makes that checkable at a glance.
 */
import type { Money, Receipt } from "@cashkit/api-types";

import { shortDate } from "../../ui/provenance";

export interface Figure {
  label: string;
  value: Money;
  /** A period the figure belongs to, when the payload names one. */
  period?: string | null;
}

function isMoney(value: unknown): value is Money {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Money).exact === "string" &&
    typeof (value as Money).display === "string"
  );
}

const LABELS: Record<string, string> = {
  closing_balance: "Closing balance",
  min_cash: "Lowest",
  net_cash: "Net cash",
  opening_balance: "Opening balance",
  total: "Total",
  total_inflow: "Money in",
  total_outflow: "Money out",
  total_accrual: "Accrual",
  depth: "Below zero by",
};

function humanize(key: string): string {
  const known = LABELS[key];
  if (known) return known;
  return key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/**
 * The figures worth putting on the card, in the order they should read.
 *
 * `summary` is unpacked to the two figures the receipt language leads with —
 * where the money ends up, and how low it goes — because those are the two the
 * user asked about in almost every read turn.
 */
export function receiptFigures(receipt: Receipt): Figure[] {
  const payload = receipt.payload as Record<string, unknown>;
  const figures: Figure[] = [];

  const summary = payload["summary"];
  if (summary && typeof summary === "object") {
    const s = summary as Record<string, unknown>;
    const closing = s["closing_balance"];
    if (isMoney(closing)) figures.push({ label: "Closing balance", value: closing });
    const min = s["min_cash"];
    if (isMoney(min)) {
      const period = typeof s["min_cash_period"] === "string" ? (s["min_cash_period"] as string) : null;
      figures.push({ label: period ? `Lowest · ${shortDate(period)}` : "Lowest", value: min, period });
    }
  }

  for (const [key, value] of Object.entries(payload)) {
    if (key === "summary") continue;
    if (isMoney(value)) {
      const periodKey = `${key}_period`;
      const period = typeof payload[periodKey] === "string" ? (payload[periodKey] as string) : null;
      figures.push({
        label: period ? `${humanize(key)} · ${shortDate(period)}` : humanize(key),
        value,
        period,
      });
    }
  }

  // R5 `top_categories` — one row per ranked category, in the order the host
  // composed them. The ranking is the engine's, not the card's.
  const categories = payload["categories"];
  if (Array.isArray(categories)) {
    for (const entry of categories) {
      if (entry && typeof entry === "object") {
        const row = entry as Record<string, unknown>;
        if (isMoney(row["total"]) && typeof row["category"] === "string") {
          figures.push({ label: row["category"] as string, value: row["total"] as Money });
        }
      }
    }
  }

  return figures;
}

/** Non-money facts a receipt carries that still belong on the card. */
export function receiptNotes(receipt: Receipt): string[] {
  const payload = receipt.payload as Record<string, unknown>;
  const notes: string[] = [];
  if (typeof payload["runway_periods"] === "number" || payload["runway_periods"] === null) {
    const end = payload["runway_end"];
    notes.push(
      typeof end === "string"
        ? `Runway to ${shortDate(end)}`
        : "Cash does not run out inside the horizon",
    );
  }
  const items = payload["items"];
  if (Array.isArray(items) && items.length > 0 && typeof items[0] === "string") {
    notes.push(`Items: ${(items as string[]).join(", ")}`);
  }
  return notes;
}
