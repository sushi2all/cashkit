/**
 * The invariant the whole product rests on, checked in a browser:
 * **every money figure on screen is a string the service produced.**
 *
 * The lint rule stops the client computing one. This checks the result rather
 * than the mechanism: it records every `display` and `exact` value in every API
 * response the page received, then reads every money-shaped token out of the
 * rendered DOM and requires each to be one of them. A figure the service never
 * sent — rounded, re-added, re-derived, or invented — fails here even if it got
 * past the linter.
 */
import { expect, test, type Page } from "@playwright/test";

import { GYM_INTENT, scriptModel, seedBook, seedItems, signIn } from "./support";

/** Every money string the service sent this page, in both its forms. */
function collectServiceFigures(page: Page): Set<string> {
  const seen = new Set<string>();
  const walk = (node: unknown): void => {
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (node && typeof node === "object") {
      const record = node as Record<string, unknown>;
      if (typeof record.exact === "string" && typeof record.display === "string") {
        seen.add(record.exact);
        seen.add(record.display);
      }
      Object.values(record).forEach(walk);
    }
  };
  page.on("response", (response) => {
    if (!response.url().includes("/api/")) return;
    void response
      .json()
      .then(walk)
      .catch(() => undefined);
  });
  return seen;
}

/**
 * Undo the presentation and recover the digits: strip the currency symbol and
 * the thin thousands separators, and turn the typographic minus back into the
 * hyphen the service emits. What is left must be exactly what arrived.
 */
function normalize(rendered: string): string {
  return rendered.replace(/€/g, "").replace(/\u2009/g, "").replace(/\u2212/g, "-").trim();
}

test("every money figure on screen is a string the service sent", async ({ page, request }) => {
  const figures = collectServiceFigures(page);
  const email = `money-${Date.now()}@example.com`;

  const token = await signIn(page, request, email);
  await seedBook(request, token);
  await seedItems(request, token);

  await scriptModel(request, [
    { kind: "answer", reply: "I will add a gym membership.", intents: [GYM_INTENT] },
  ]);

  await page.goto("/");
  await expect(page.getByTestId("home-screen-balance")).toBeVisible();
  await page.getByTestId("home-screen-ask-input").fill("I joined a gym, 49.90 a month from April");
  await page.getByTestId("home-screen-ask-send").click();
  await expect(page.locator('[data-testid^="proposal-card-"]').first()).toBeVisible();

  await page.getByTestId("home-screen-forecast-link").click();
  await expect(page.getByTestId("forecast-screen-table-card")).toBeVisible();

  // Everything the page has rendered by now: header, sparkline label, the
  // proposal card's deltas block, and the whole forecast table.
  const rendered = await page.evaluate(() => document.body.innerText);
  // Two shapes, and the second one matters: anything carrying the currency
  // symbol, **with or without decimals**, plus any bare decimal figure. A
  // pattern that only matched decimal-bearing tokens would miss the exact
  // failure this test exists to catch — a client that rounded the cents away
  // renders "\u2009"-grouped text like "EUR 2 500", which is not a token at all
  // under that pattern.
  const tokens =
    rendered.match(/[\u2212-]?€[\d \u2009]+(?:\.\d{2,4})?|[\u2212-]?[\d \u2009]+\.\d{2,4}/g) ??
    [];
  expect(tokens.length, "the page should be showing money at all").toBeGreaterThan(5);
  expect(figures.size, "the service should have sent money").toBeGreaterThan(5);

  const strangers = tokens.map(normalize).filter((token) => !figures.has(token));
  expect(strangers, `figures on screen that the service never sent: ${strangers.join(", ")}`).toEqual([]);
});
