/**
 * Rendering primitives for money. **Nothing here computes a money value.**
 *
 * Money reaches the client as `{exact, display}` (D-MLP-06): `exact` is the
 * engine's lossless 4dp string, `display` the same figure at 2dp rounded the
 * way the engine rounds. Both are produced by the service's one canonical
 * serializer. The client's entire relationship with them is to put them on a
 * screen unchanged.
 *
 * Two things this module deliberately does not do:
 *
 *  * **It does not drop the cents.** `design.pen` shows whole euros ("€7,412")
 *    because a mock has no engine behind it. Rendering `7412.38` as `€7,412`
 *    is a rounding the engine did not perform, and the app's whole claim is
 *    that the number on the screen is the number the engine computed. So the
 *    2dp `display` string is rendered in full (D-MLP-41).
 *  * **It does not parse.** Grouping separators are inserted into the digit
 *    string by string manipulation; the digits themselves are never altered,
 *    reordered or recomputed. Sign detection reads the leading character, it
 *    does not compare against zero.
 */
import type { Money } from "@cashkit/api-types";

/** The MLP is EUR-only (SPEC §1); the symbol is a label, not a conversion. */
export const CURRENCY_SYMBOL = "€";

/** Both the ASCII hyphen the service emits and the typographic minus. */
const MINUS = ["-", "\u2212"];

/**
 * The thousands separator: a thin space (U+2009), not a comma or a full space.
 * A comma is a decimal separator in half of Europe and this app is EUR-only
 * for an Italian-first audience, so a comma would be ambiguous exactly where
 * ambiguity is expensive. The thin space groups without asserting a locale.
 */
const GROUP_SEPARATOR = "\u2009";

/** Does this figure read as negative? A character test, never a comparison. */
export function isNegative(value: Money | null | undefined): boolean {
  if (!value) return false;
  const first = value.display.charAt(0);
  return MINUS.includes(first);
}

/**
 * Insert thin thousands separators into the integer digits of a decimal
 * string. Pure string work: every digit that goes in comes out, in order.
 */
function group(digits: string): string {
  let out = "";
  let seen = 0;
  for (let i = digits.length - 1; i >= 0; i -= 1) {
    out = digits.charAt(i) + out;
    seen += 1;
    if (seen % 3 === 0 && i > 0) out = GROUP_SEPARATOR + out;
  }
  return out;
}

/**
 * The figure as the user reads it: sign, currency symbol, grouped digits, and
 * the engine's own two decimals. The digits are the service's, untouched.
 */
export function formatMoney(value: Money | null | undefined, options?: { dash?: string }): string {
  if (!value) return options?.dash ?? "—";
  const raw = value.display;
  const negative = MINUS.includes(raw.charAt(0));
  const unsigned = negative ? raw.slice(1) : raw;
  const dot = unsigned.indexOf(".");
  const whole = dot === -1 ? unsigned : unsigned.slice(0, dot);
  const cents = dot === -1 ? "" : unsigned.slice(dot);
  return `${negative ? "\u2212" : ""}${CURRENCY_SYMBOL}${group(whole)}${cents}`;
}

/**
 * The same figure with no currency symbol — for table columns whose header
 * already carries the unit (the Forecast IN/OUT/END grid).
 */
export function formatBare(value: Money | null | undefined, options?: { dash?: string }): string {
  if (!value) return options?.dash ?? "—";
  const raw = value.display;
  const negative = MINUS.includes(raw.charAt(0));
  const unsigned = negative ? raw.slice(1) : raw;
  const dot = unsigned.indexOf(".");
  const whole = dot === -1 ? unsigned : unsigned.slice(0, dot);
  const cents = dot === -1 ? "" : unsigned.slice(dot);
  return `${negative ? "\u2212" : ""}${group(whole)}${cents}`;
}

/**
 * The lossless 4dp string, for the one place full precision belongs: the
 * Trace screen's engine panel and its step rows (SPEC §6-S5, D-MLP-06).
 */
export function exactOf(value: Money | null | undefined): string {
  return value ? value.exact : "—";
}

/** The ink a figure is set in. Negative figures are rust; that is the only rule. */
export function moneyTone(value: Money | null | undefined): "ink" | "rust" {
  return isNegative(value) ? "rust" : "ink";
}
