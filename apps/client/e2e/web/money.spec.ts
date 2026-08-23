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

import { GYM_INTENT, scriptModel, seedBook, seedFork, seedItems, signIn } from "./support";

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
      // The reconciliation report carries two figures that are NOT engine
      // money and are deliberately not shaped like it: `sheet_value` is a
      // spreadsheet cell, verbatim, and `delta` is the comparison between the
      // two systems (D-MLP-78). They are still strings the service sent, and
      // the screen renders them unchanged, so they belong in the allow-list —
      // named, and only these two.
      for (const field of ["sheet_value", "delta"] as const) {
        const value = record[field];
        if (typeof value === "string") seen.add(value);
      }
      Object.values(record).forEach(walk);
    }
  };
  page.on("response", (response) => {
    if (!response.url().includes("/api/")) return;
    const type = response.headers()["content-type"] ?? "";
    if (type.includes("text/event-stream")) {
      // An import's figures arrive as server-sent events, so `response.json()`
      // would drop the entire reconciliation report out of this check.
      void response
        .text()
        .then((body) => {
          for (const line of body.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            try {
              walk(JSON.parse(line.slice(6)));
            } catch {
              /* a partial frame is not a payload */
            }
          }
        })
        .catch(() => undefined);
      return;
    }
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

/** The pattern that recovers a money-shaped token from rendered text. */
const MONEY_TOKEN =
  /[\u2212-]?\u20ac[\d \u2009]+(?:\.\d{2,4})?|[\u2212-]?[\d \u2009]+\.\d{2,4}/g;

/** Every money-shaped token on the page that the service never sent. */
async function strangersOn(page: Page, figures: Set<string>): Promise<string[]> {
  const rendered = await page.evaluate(() => document.body.innerText);
  const tokens = rendered.match(MONEY_TOKEN) ?? [];
  return tokens.map(normalize).filter((token) => !figures.has(token));
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

/**
 * The same invariant on the screens S4 added.
 *
 * A new screen that invents a figure — a subtotal it added up, a balance it
 * interpolated, a percentage it turned back into euros — fails here even if it
 * got past the linter. That is the point of checking the result as well as the
 * mechanism (D-MLP-50), and it is why this test walks every new surface rather
 * than trusting that the rule covered them.
 */
test("the scenarios, actuals and plan screens invent no figures either", async ({
  page,
  request,
}) => {
  const figures = collectServiceFigures(page);
  const token = await signIn(page, request, `money-s4-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  await seedFork(request, token, "car", [
    {
      op: "add_item",
      id: "car_loan",
      name: "Car loan",
      direction: "out",
      amount: "-620.00",
      recurrence: "1m",
      start: "2026-06-01",
    },
  ]);

  // An actual, so the ledger rows, the recorded total and the reconciliation
  // all have something in them.
  await scriptModel(request, [
    {
      kind: "answer",
      reply: "Recording that.",
      intents: [{ op: "add_event", date: "2026-03-09", amount: "-96.00", note: "groceries" }],
    },
  ]);
  await page.goto("/actuals");
  await expect(page.getByTestId("actuals-screen-recorded-card")).toBeVisible();
  await page.getByTestId("actuals-screen-ask-input").fill("groceries on the 9th were 96");
  await page.getByTestId("actuals-screen-ask-send").click();
  await page.getByTestId("actuals-screen-proposal-card-apply").click();
  await expect(page.getByTestId("actuals-screen-resolution")).toContainText("APPLIED");

  // Actuals: ledger rows, the correction annotation, the recorded total, the
  // still-computed rows and the month-end figure.
  await expect(page.getByTestId("actuals-screen-recorded-total")).toBeVisible();
  await expect(page.getByTestId("actuals-screen-month-end")).toBeVisible();
  expect(await strangersOn(page, figures), "on Actuals").toEqual([]);

  // Scenarios: both compare columns, every delta, the diverge label.
  await page.goto("/scenarios");
  await expect(page.getByTestId("scenarios-screen-table-card")).toBeVisible();
  expect(await strangersOn(page, figures), "on Scenarios").toEqual([]);

  // Plan vs actual: the summary strip, every row, every plan and delta stamp —
  // in both groupings, because the category view is where a subtotal would be
  // most tempting to add up.
  await page.goto("/plan");
  await expect(page.getByTestId("plan-screen-summary-card")).toBeVisible();
  expect(await strangersOn(page, figures), "on Plan vs actual, by item").toEqual([]);
  await page.getByTestId("plan-screen-toggle-category").click();
  await expect(page.getByTestId("plan-screen-toggle-category")).toBeVisible();
  expect(await strangersOn(page, figures), "on Plan vs actual, by category").toEqual([]);
});

/** The item, event and settings surfaces, held to the same rule. */
test("the item, event and settings screens invent no figures either", async ({ page, request }) => {
  const figures = collectServiceFigures(page);
  const token = await signIn(page, request, `money-item-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);

  const auth = { Authorization: `Bearer ${token}` };
  const created = await request.post("/api/book/edits", {
    headers: auth,
    data: {
      origin: "cell_edit",
      ops: [{ op: "add_event", date: "2026-10-12", amount: "-1800.00", note: "October trip" }],
    },
  });
  const proposal = ((await created.json()) as { proposal: { id: string } }).proposal;
  await request.post(`/api/proposals/${proposal.id}`, { headers: auth, data: { action: "accept" } });

  // The item screen: the rule card's amount, the next-occurrences line, every
  // segment row, and the arithmetic steps.
  await page.goto("/item?id=rent");
  await expect(page.getByTestId("item-screen-rule-card")).toBeVisible();
  expect(await strangersOn(page, figures), "on Item, recurring").toEqual([]);

  await page.getByTestId("item-screen-variant-custom").click();
  await expect(page.getByTestId("item-screen-dates-card")).toBeVisible();
  expect(await strangersOn(page, figures), "on Item, custom").toEqual([]);

  await page.goto("/event?item=rent");
  await expect(page.getByTestId("event-screen-rows-card")).toBeVisible();
  expect(await strangersOn(page, figures), "on Event").toEqual([]);

  await page.goto("/settings");
  await expect(page.getByTestId("settings-screen-about-card")).toBeVisible();
  expect(await strangersOn(page, figures), "on Settings").toEqual([]);
});


/**
 * The import and onboarding surfaces, held to the same rule.
 *
 * The reconciliation report is the most tempting place in the whole app to
 * invent a figure: it puts two systems' numbers side by side and shows the
 * difference between them. Every one of those three is the service's own.
 */
test("the import and onboarding screens invent no figures either", async ({ page, request }) => {
  const figures = collectServiceFigures(page);
  await signIn(page, request, `money-s5-${Date.now()}@example.com`);

  // Onboarding, end to end: the card's deltas block is the figure-heavy part.
  await scriptModel(request, [
    {
      kind: "answer",
      reply: "A salary and a rent.",
      intents: [
        {
          op: "add_item",
          id: "salary",
          name: "Salary",
          direction: "in",
          amount: "2617.33",
          recurrence: "1m",
          start: "2026-01-01",
        },
      ],
    },
  ]);
  await page.goto("/onboarding");
  await page.getByTestId("onboarding-screen-opening").fill("2500.00");
  await page.getByTestId("onboarding-screen-create").click();
  await page.getByTestId("onboarding-screen-text").fill("I earn 2617.33 a month");
  await page.getByTestId("onboarding-screen-send").click();
  await expect(page.getByTestId("onboarding-screen-proposal-card")).toBeVisible({
    timeout: 20_000,
  });
  expect(await strangersOn(page, figures), "on Onboarding").toEqual([]);
  await page.getByTestId("onboarding-screen-proposal-card-apply").click();
  await expect(page.getByTestId("onboarding-screen-done-card")).toBeVisible({ timeout: 20_000 });

  // Import: a run that deliberately does NOT reconcile, so the report shows a
  // sheet figure, an engine figure and a delta on the same row.
  const wrong = {
    op: "add_item",
    id: "salary_sheet",
    direction: "in",
    amount: "1500.00",
    recurrence: "1m",
    start: "2026-01-01",
  };
  await scriptModel(request, [
    {
      reply: "One income line.",
      opening_balance: null,
      horizon: null,
      sections: [{ name: "Income" }],
      // The onboarding half above put a plan in this book, so this import
      // lands in a fork and a balance row is not comparable there (D-MLP-76).
      // A monthly total is, and it is what puts a sheet figure, an engine
      // figure and a delta on one row — which is the whole point here.
      checks: [
        { ref: "Budget!C3", label: "Salary January", measure: "total_in", period: "2026-01-01" },
      ],
    },
    { kind: "answer", reply: "Authored.", intents: [wrong] },
    { kind: "answer", reply: "Authored.", intents: [wrong] },
    { kind: "answer", reply: "Authored.", intents: [wrong] },
  ]);

  const sheet = await request.get("/__control/workbook?kind=decimals");
  await page.goto("/import");
  await page.locator('input[data-testid="import-file-input"]').setInputFiles({
    name: "budget.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from(await sheet.body()),
  });
  await expect(page.getByTestId("import-screen-report")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("import-screen-report-check-0-delta")).toBeVisible();
  expect(await strangersOn(page, figures), "on Import").toEqual([]);
});
