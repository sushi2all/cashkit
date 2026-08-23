/**
 * Plan vs actual (SPEC §6-S8): the bar, the tick, and the empty track.
 *
 * "Bars encode percent-of-plan with a 100% tick; amounts stay on the row;
 * **unsettled = empty track, never a fake bar**." That last clause is the one
 * with teeth. `reconciliation.actual` is `0.0000` both for a row nothing has
 * happened to yet and for a row that genuinely came to nothing, and a bar drawn
 * at zero says the second where the truth is the first. The two must not look
 * alike, so this test checks that an unsettled row has no fill element at all.
 *
 * The category view is tested for what it does *not* do: it groups, and it does
 * not add up. A subtotal is a sum of money and the client never computes one
 * (D-MLP-62).
 */
import { expect, test } from "@playwright/test";

import { readJson, seedBook, seedItems, signIn } from "./support";

interface Reconcile {
  reconciliation: {
    lines: { item_id: string; forecast: { display: string }; actual: { display: string }; drift: { display: string } }[];
    forecast_total: { display: string };
    actual_total: { display: string };
    drift_total: { display: string };
  };
}

/** Record one actual against `rent`, so exactly one line has settled. */
async function settleRent(request: import("@playwright/test").APIRequestContext, token: string) {
  const auth = { Authorization: `Bearer ${token}` };
  const created = await request.post("/api/book/edits", {
    headers: auth,
    data: {
      origin: "cell_edit",
      context: "actuals_record",
      ops: [
        { op: "record_actual", date: "2026-03-05", amount: "-1000.00", item: "rent", note: "rent" },
      ],
    },
  });
  expect([200, 201], await created.text()).toContain(created.status());
  const proposal = ((await created.json()) as { proposal: { id: string } }).proposal;
  const accept = await request.post(`/api/proposals/${proposal.id}`, {
    headers: auth,
    data: { action: "accept" },
  });
  expect(await accept.text()).toContain('"kind":"applied"');
}

test("an unsettled row is an empty track, and a settled one is a bar", async ({ page, request }) => {
  const token = await signIn(page, request, `plan-bar-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  await settleRent(request, token);

  await page.goto("/plan");
  await expect(page.getByTestId("plan-screen-summary-card")).toBeVisible();

  // Rent has a ledger row this window, so it has a percentage and a bar.
  await expect(page.getByTestId("plan-screen-bar-rent-fill")).toBeVisible();
  await expect(page.getByTestId("plan-screen-bar-rent-tick")).toBeVisible();
  await expect(page.getByTestId("plan-screen-delta-rent")).not.toContainText("NOT SETTLED");

  // Salary has none, so it has no percentage — and therefore no fill at all,
  // rather than a fill of zero width.
  await expect(page.getByTestId("plan-screen-bar-salary")).toBeVisible();
  await expect(page.getByTestId("plan-screen-bar-salary-tick")).toBeVisible();
  await expect(page.getByTestId("plan-screen-bar-salary-fill")).toHaveCount(0);
  await expect(page.getByTestId("plan-screen-delta-salary")).toContainText("NOT SETTLED");
  // And the amount column says absent, not zero.
  await expect(page.getByTestId("plan-screen-row-salary-value")).toHaveText("—");
});

test("every figure on the row is the reconciliation's own", async ({ page, request }) => {
  const token = await signIn(page, request, `plan-figs-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  await settleRent(request, token);

  const report = (
    await readJson<Reconcile>(request, token, "/book/reconcile?since=2026-03-01&until=2026-03-17")
  ).reconciliation;

  await page.goto("/plan");
  await expect(page.getByTestId("plan-screen-summary-card")).toBeVisible();

  const normalize = (text: string) =>
    text.replace(/\u20ac/g, "").replace(/\u2009/g, "").replace(/\u2212/g, "-").trim();

  for (const [testId, expected] of [
    ["plan-screen-plan-total-value", report.forecast_total.display],
    ["plan-screen-actual-total-value", report.actual_total.display],
    ["plan-screen-drift-total-value", report.drift_total.display],
  ] as const) {
    expect(normalize((await page.getByTestId(testId).textContent()) ?? "")).toBe(expected);
  }

  const rent = report.lines.find((l) => l.item_id === "rent");
  expect(rent, "the fixture settles rent").toBeTruthy();
  expect(normalize((await page.getByTestId("plan-screen-plan-rent").textContent()) ?? "")).toContain(
    rent!.forecast.display,
  );
});

test("the category view groups and does not add up", async ({ page, request }) => {
  const token = await signIn(page, request, `plan-cat-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  await settleRent(request, token);

  await page.goto("/plan");
  await page.getByTestId("plan-screen-toggle-category").click();

  // A category header with no subtotal, and a line saying why rather than a
  // figure nobody computed.
  const note = page.locator('[data-testid$="-no-subtotal"]').first();
  await expect(note).toBeVisible();
  await expect(note).toContainText("NO SUBTOTAL");

  // The rows themselves are still there, with their own engine figures.
  await expect(page.getByTestId("plan-screen-row-rent")).toBeVisible();
  await expect(page.getByTestId("plan-screen-row-salary")).toBeVisible();
});
