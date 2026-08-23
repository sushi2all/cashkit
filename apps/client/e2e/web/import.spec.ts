/**
 * Screen 14 in a browser — the UI half of T16 (SPEC §6-S14, §7).
 *
 * The service half is `apps/service/trials/t16_import_round_trip.py`, which
 * runs the pinned model against the T06 and T07 workbooks. This is the other
 * half of the same gate: the round trip **through the UI**, with the
 * reconciliation report on the screen, the non-empty-book fork rule visible
 * before the file goes anywhere, and the call cap reported honestly.
 *
 * The provider is scripted, and only the provider (D-MLP-34). What is scripted
 * here is what a live model cannot be asked to do on cue: get a section wrong
 * three times, or fail to reconcile twenty times in a row.
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { readJson, scriptModel, seedBook, seedItems, signIn } from "./support";

const SALARY_OP = {
  op: "add_item",
  id: "salary_sheet",
  name: "Salary",
  direction: "in",
  amount: "2000.00",
  recurrence: "1m",
  start: "2026-01-01",
  tags: { cat: "income" },
};

/** The plan call: the sections, and which cells are the sheet's own totals. */
function plan(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    reply: "A twelve-month budget with one income line.",
    opening_balance: { cell: "Budget!C2" },
    horizon: null,
    sections: [{ name: "Income", where: "row 3" }],
    checks: [
      {
        ref: "Budget!C5",
        label: "Closing January",
        measure: "closing",
        period: "2026-01-01",
      },
      {
        ref: "Budget!N5",
        label: "Closing December",
        measure: "closing",
        period: "2026-12-01",
      },
    ],
    ...overrides,
  };
}

function authored(...intents: Record<string, unknown>[]): Record<string, unknown> {
  return { kind: "answer", reply: "Authored the section.", intents };
}

async function workbook(
  request: APIRequestContext,
  kind: "simple" | "messy" = "simple",
): Promise<Buffer> {
  const response = await request.get(`/__control/workbook?kind=${kind}`);
  expect(response.ok()).toBeTruthy();
  return Buffer.from(await response.body());
}

/** Upload through the real picker, exactly as a person does. */
async function upload(page: Page, bytes: Buffer, name = "budget.xlsx"): Promise<void> {
  await page.locator('input[data-testid="import-file-input"]').setInputFiles({
    name,
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: bytes,
  });
}

async function items(request: APIRequestContext, token: string, scenario = "base"): Promise<string[]> {
  const state = await readJson<{ items: { id: string }[] }>(
    request,
    token,
    `/book/state?scenario=${scenario}`,
  );
  return state.items.map((item) => item.id).sort();
}

test("an import reconciles on screen and lands only when the card is applied", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `import-${Date.now()}@example.com`);
  await seedBook(request, token);
  await scriptModel(request, [plan(), authored(SALARY_OP)]);

  await page.goto("/import");
  await expect(page.getByTestId("import-screen-import-card")).toBeVisible();
  await expect(page.getByTestId("import-screen-empty-example")).toContainText("try:");

  // Count the confirmations at the network, not at the DOM: an optimistic
  // apply races a DOM assertion and can win (D-MLP-72).
  let confirmations = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && /\/api\/proposals\//.test(r.url())) confirmations += 1;
  });

  await upload(page, await workbook(request));

  await expect(page.getByTestId("import-screen-report")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("import-screen-report-summary")).toContainText("2 matched");
  await expect(page.getByTestId("import-screen-report-summary")).toContainText("0 mismatched");
  await expect(page.getByTestId("import-screen-report-target")).toContainText("INTO THE PLAN");
  await expect(page.getByTestId("import-screen-report-calls")).toContainText("OF 20 ASSISTANT CALLS");

  // Per sheet row: the cell it checked, both figures, and the verdict.
  await expect(page.getByTestId("import-screen-report-check-0-status")).toHaveText("MATCHED");
  await expect(page.getByTestId("import-screen-report-check-0-sheet")).toContainText("sheet 4500");
  await expect(page.getByTestId("import-screen-report-check-0-engine")).toContainText("4 500.00");

  // The progress stream was rendered as it happened, not only at the end.
  await expect(page.getByTestId("import-screen-progress-card")).toContainText("Reading budget.xlsx");
  await expect(page.getByTestId("import-screen-progress-card")).toContainText("Section 1 of 1");

  // The card is the change (ADR-0029): nothing is in the book yet.
  await expect(page.getByTestId("import-screen-proposal-card")).toBeVisible();
  expect(await items(request, token)).toEqual([]);
  expect(confirmations, "an import must not confirm its own card").toBe(0);

  await page.getByTestId("import-screen-proposal-card-apply").click();
  await expect(page.getByTestId("import-screen-resolution")).toContainText("APPLIED");
  expect(confirmations).toBe(1);
  expect(await items(request, token)).toEqual(["salary_sheet"]);
});

test("a book that already has a plan says so before the file goes anywhere", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `import-fork-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  const before = await items(request, token);
  expect(before).toEqual(["rent", "salary"]);

  await scriptModel(request, [
    plan({ opening_balance: null, checks: [] }),
    authored(SALARY_OP),
  ]);

  await page.goto("/import");
  await upload(page, await workbook(request), "Family Budget 2026.xlsx");

  // SPEC §7.3 on the screen, before the report and before the card.
  await expect(page.getByTestId("import-screen-target")).toContainText(
    "NEW SCENARIO FAMILY-BUDGET-2026",
    { timeout: 30_000 },
  );
  await expect(page.getByTestId("import-screen-target-message")).toContainText(
    "Base is left exactly as it is.",
  );

  await expect(page.getByTestId("import-screen-report")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("import-screen-report-target")).toContainText(
    "INTO SCENARIO FAMILY-BUDGET-2026 · BASE UNTOUCHED",
  );

  await page.getByTestId("import-screen-proposal-card-apply").click();
  await expect(page.getByTestId("import-screen-resolution")).toContainText("APPLIED");

  // Base did not move; the fork exists and holds the import.
  expect(await items(request, token), "base was changed by an import").toEqual(before);
  const fork = await items(request, token, "family-budget-2026");
  expect(fork).toContain("salary_sheet");
  expect(fork).toEqual(expect.arrayContaining(before));

  const scenarios = await readJson<{ scenarios: { id: string }[] }>(
    request,
    token,
    "/book/scenarios",
  );
  expect(scenarios.scenarios.map((s) => s.id)).toContain("family-budget-2026");
});

test("a divergence is shown with both figures and is never smoothed away", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `import-miss-${Date.now()}@example.com`);
  await seedBook(request, token);
  const wrong = { ...SALARY_OP, amount: "1500.00" };
  await scriptModel(request, [plan(), authored(wrong), authored(wrong), authored(wrong)]);

  await page.goto("/import");
  await upload(page, await workbook(request));
  await expect(page.getByTestId("import-screen-report")).toBeVisible({ timeout: 30_000 });

  await expect(page.getByTestId("import-screen-report-summary")).toContainText("2 mismatched");
  await expect(page.getByTestId("import-screen-report-check-0-status")).toHaveText("MISMATCHED");
  await expect(page.getByTestId("import-screen-report-check-0-sheet")).toContainText("sheet 4500");
  await expect(page.getByTestId("import-screen-report-check-0-engine")).toContainText("4 000.00");
  await expect(page.getByTestId("import-screen-report-check-0-delta")).toContainText("-500");
  await expect(page.getByTestId("import-screen-report-check-0-note")).toContainText(
    "the sheet says 4500 and the engine computes 4000",
  );

  // A mismatch is not a dead end: the partial result is still a card to read.
  await expect(page.getByTestId("import-screen-proposal-card")).toBeVisible();
});

test("the call cap is reported honestly and still hands back what it worked out", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `import-cap-${Date.now()}@example.com`);
  await seedBook(request, token);
  const wrong = { ...SALARY_OP, amount: "1.00" };
  const sections = Array.from({ length: 10 }, (_unused, index) => ({ name: `section ${index}` }));
  await scriptModel(request, [
    plan({ sections }),
    ...Array.from({ length: 40 }, () => authored(wrong)),
  ]);

  await page.goto("/import");
  await upload(page, await workbook(request));
  await expect(page.getByTestId("import-screen-report")).toBeVisible({ timeout: 60_000 });

  await expect(page.getByTestId("import-screen-report-calls")).toContainText(
    "20 OF 20 ASSISTANT CALLS · CAP REACHED",
  );
  await expect(page.getByTestId("import-screen-report-incomplete")).toContainText(
    "limit of 20 assistant calls",
  );
  // Honest, not silent: the partial result is a card, and it is still unapplied.
  await expect(page.getByTestId("import-screen-proposal-card")).toBeVisible();
  expect(await items(request, token)).toEqual([]);
});

test("the report carries the WHAT-IF stamp, because every figure in it is a dry run", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `import-stamp-${Date.now()}@example.com`);
  await seedBook(request, token);
  await scriptModel(request, [plan(), authored(SALARY_OP)]);

  await page.goto("/import");
  await upload(page, await workbook(request));
  await expect(page.getByTestId("import-screen-report")).toBeVisible({ timeout: 30_000 });

  await expect(page.getByTestId("import-screen-whatif")).toContainText("WHAT-IF");
  await expect(page.getByTestId("import-screen-whatif")).toContainText("INCLUDES PENDING");
  await expect(page.getByTestId("import-screen-provenance")).toContainText("ENGINE");
  await expect(page.getByTestId("import-screen-report-legend")).toContainText(
    "SHEET FIGURES ARE THE WORKBOOK",
  );
});

test("export downloads the workbook the service produced", async ({ page, request }) => {
  const token = await signIn(page, request, `export-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);

  await page.goto("/import");
  await expect(page.getByTestId("import-screen-export-card")).toBeVisible();
  await page.getByTestId("import-screen-months-6").click();

  const download = page.waitForEvent("download");
  await page.getByTestId("import-screen-download").click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("cashkit-budget.xlsx");
  await expect(page.getByTestId("import-screen-export-note")).toContainText("cashkit-budget.xlsx");

  // The ledger mode is the same control, and it changes what is exported.
  await page.getByTestId("import-screen-mode-ledger").click();
  const ledger = page.waitForEvent("download");
  await page.getByTestId("import-screen-download").click();
  expect((await ledger).suggestedFilename()).toBe("cashkit-ledger.xlsx");
});
