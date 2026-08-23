/**
 * Screen 13 in a browser — the first book (SPEC §6-S13).
 *
 * The gate clause is "onboarding produces an applied proposal, never a silent
 * book", and both halves of that sentence are tested here. A book arrives
 * empty from step (a) and stays empty until a card raised by step (b) is
 * applied in step (c); the skip path leaves it empty, which is a real state
 * and not a failure.
 *
 * The book is checked **through the API**, not by reading the screen: a screen
 * that renders nothing and a book that holds nothing look the same, and only
 * one of them is what ADR-0029 requires.
 */
import { expect, test, type APIRequestContext } from "@playwright/test";

import { readJson, scriptModel, signIn } from "./support";

const SALARY_INTENT = {
  op: "add_item",
  id: "salary",
  name: "Salary",
  direction: "in",
  amount: "2400.00",
  recurrence: "1m",
  start: "2026-01-01",
};

const RENT_INTENT = {
  op: "add_item",
  id: "rent",
  name: "Rent",
  direction: "out",
  amount: "-900.00",
  recurrence: "1m",
  start: "2026-01-01",
};

async function items(request: APIRequestContext, token: string): Promise<string[]> {
  const state = await readJson<{ items: { id: string }[] }>(request, token, "/book/state");
  return state.items.map((item) => item.id).sort();
}

async function hasBook(request: APIRequestContext, token: string): Promise<boolean> {
  const response = await request.get("/api/book/state", {
    headers: { Authorization: `Bearer ${token}` },
  });
  return response.status() === 200;
}

test("a signed-in account with no book lands on the wizard", async ({ page, request }) => {
  const token = await signIn(page, request, `onboard-${Date.now()}@example.com`);
  expect(await hasBook(request, token)).toBe(false);

  await page.goto("/");
  await expect(page.getByTestId("onboarding-screen")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("onboarding-screen-step")).toContainText("STEP 1 OF 3");
});

test("onboarding produces an applied proposal, never a silent book", async ({ page, request }) => {
  const token = await signIn(page, request, `onboard-apply-${Date.now()}@example.com`);
  await scriptModel(request, [
    {
      kind: "answer",
      reply: "That is a salary and a rent.",
      intents: [SALARY_INTENT, RENT_INTENT],
    },
  ]);

  let confirmations = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && /\/api\/proposals\//.test(r.url())) confirmations += 1;
  });

  await page.goto("/onboarding");
  await expect(page.getByTestId("onboarding-screen-book-card")).toBeVisible();

  // Step (a): the book is created immediately, and it is empty.
  await page.getByTestId("onboarding-screen-opening").fill("1250.00");
  await page.getByTestId("onboarding-screen-create").click();
  await expect(page.getByTestId("onboarding-screen-describe-card")).toBeVisible();
  expect(await hasBook(request, token)).toBe(true);
  expect(await items(request, token), "step (a) must not author anything").toEqual([]);

  // Step (b): an ordinary turn, and an ordinary card.
  await page
    .getByTestId("onboarding-screen-text")
    .fill("I earn 2400 a month and my rent is 900");
  await page.getByTestId("onboarding-screen-send").click();
  await expect(page.getByTestId("onboarding-screen-proposal-card")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId("onboarding-screen-proposal-card-op-0")).toContainText("Salary");
  await expect(page.getByTestId("onboarding-screen-proposal-card-op-1")).toContainText("Rent");

  // The card is the change: nothing is in the book, and nothing was confirmed.
  expect(await items(request, token), "the wizard must not apply its own card").toEqual([]);
  expect(confirmations).toBe(0);

  // Step (c): apply, and only then is the book populated.
  await page.getByTestId("onboarding-screen-proposal-card-apply").click();
  await expect(page.getByTestId("onboarding-screen-done-card")).toBeVisible({ timeout: 20_000 });
  expect(confirmations).toBe(1);
  expect(await items(request, token)).toEqual(["rent", "salary"]);
  await expect(page.getByTestId("onboarding-screen-done-lines-value")).toHaveText("2");

  await page.getByTestId("onboarding-screen-finish").click();
  await expect(page.getByTestId("home-screen-balance")).toBeVisible({ timeout: 20_000 });
});

test("the skip path leaves an empty book, and an empty book is a real book", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `onboard-skip-${Date.now()}@example.com`);
  await page.goto("/onboarding");
  await page.getByTestId("onboarding-screen-opening").fill("0.00");
  await page.getByTestId("onboarding-screen-create").click();
  await expect(page.getByTestId("onboarding-screen-describe-card")).toBeVisible();

  await page.getByTestId("onboarding-screen-skip").click();
  await expect(page.getByTestId("home-screen-balance")).toBeVisible({ timeout: 20_000 });
  expect(await hasBook(request, token)).toBe(true);
  expect(await items(request, token)).toEqual([]);
});

test("a discarded card leaves the book empty and the wizard usable", async ({ page, request }) => {
  const token = await signIn(page, request, `onboard-discard-${Date.now()}@example.com`);
  await scriptModel(request, [
    { kind: "answer", reply: "A salary.", intents: [SALARY_INTENT] },
  ]);

  await page.goto("/onboarding");
  await page.getByTestId("onboarding-screen-opening").fill("500.00");
  await page.getByTestId("onboarding-screen-create").click();
  await page.getByTestId("onboarding-screen-text").fill("I earn 2400 a month");
  await page.getByTestId("onboarding-screen-send").click();
  await expect(page.getByTestId("onboarding-screen-proposal-card")).toBeVisible({
    timeout: 20_000,
  });

  await page.getByTestId("onboarding-screen-proposal-card-discard").click();
  await expect(page.getByTestId("onboarding-screen-proposal-card")).toBeHidden();
  expect(await items(request, token)).toEqual([]);
  await expect(page.getByTestId("onboarding-screen-describe-card")).toBeVisible();
});

test("a clarification asks and stores nothing", async ({ page, request }) => {
  const token = await signIn(page, request, `onboard-clarify-${Date.now()}@example.com`);
  await scriptModel(request, [
    { kind: "clarification", reply: "When does the rent start?", intents: [] },
  ]);

  await page.goto("/onboarding");
  await page.getByTestId("onboarding-screen-opening").fill("0.00");
  await page.getByTestId("onboarding-screen-create").click();
  await page.getByTestId("onboarding-screen-text").fill("rent is 900");
  await page.getByTestId("onboarding-screen-send").click();

  await expect(page.getByTestId("onboarding-screen-reply")).toContainText(
    "When does the rent start?",
    { timeout: 20_000 },
  );
  await expect(page.getByTestId("onboarding-screen-proposal-card")).toBeHidden();
  expect(await items(request, token)).toEqual([]);
});
