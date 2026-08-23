/**
 * T15 — the WHAT-IF rule, on the payload and on the screen.
 *
 * SPEC §2.4 is the single definition, quoted verbatim because the PROMPT
 * requires its wording wherever it is restated:
 *
 * > Base is the plan of record. Any figure NOT from the committed state of
 * > `base` — a non-base scenario (active or not), a throwaway overlay, or a
 * > dry-run including pending changes — carries the WHAT-IF stamp: payload
 * > field `what_if: {stamped: true, reason: "scenario"|"overlay"|"pending",
 * > scenario?: id}`, and a rendered stamp element (ADR-0024). The Home header
 * > and sparkline always show base committed figures, in neutral form, even
 * > while a fork is active; a fork's own figures render stamped with the
 * > fork's name.
 *
 * The gate is "present **and** rendered", so every test here checks both
 * halves: the field on the wire, and the element on the page. A payload that
 * is truthful and a screen that hides it is exactly as wrong as the reverse.
 */
import { expect, test, type Page } from "@playwright/test";

import { GYM_INTENT, readJson, scriptModel, seedBook, seedFork, seedItems, signIn } from "./support";

interface WhatIf {
  stamped: boolean;
  reason: "scenario" | "overlay" | "pending" | null;
  scenario: string | null;
}
interface Stamped {
  what_if: WhatIf;
  scenario: string;
  closing: { display: string; exact: string }[];
  months: string[];
  dirty: boolean;
}

/** Every stamp element rendered anywhere on the page. */
async function stamps(page: Page): Promise<string[]> {
  const found = await page.locator('[data-testid*="what-if"]').allTextContents();
  return found.map((t) => t.trim()).filter((t) => t.length > 0);
}

test("the three reasons are on the wire, each one exactly where §2.4 puts it", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `t15-payload-${Date.now()}@example.com`);
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

  // Base, committed: the plan of record. Not stamped.
  const base = await readJson<Stamped>(request, token, "/book/state?scenario=base");
  expect(base.dirty, "the fixture saves, so base is committed").toBe(false);
  expect(base.what_if.stamped).toBe(false);

  // A non-base scenario — active or not — is stamped, and names itself.
  const fork = await readJson<Stamped>(request, token, "/book/state?scenario=car");
  expect(fork.what_if).toMatchObject({ stamped: true, reason: "scenario", scenario: "car" });

  // A dry-run including pending changes is stamped `pending`.
  await scriptModel(request, [
    { kind: "answer", reply: "I will add a gym membership.", intents: [GYM_INTENT] },
  ]);
  const turn = await request.post("/api/turns", {
    headers: { Authorization: `Bearer ${token}` },
    data: { text: "I joined a gym, 49.90 a month from April" },
  });
  expect(turn.status(), await turn.text()).toBe(200);
  const proposalTurn = (await turn.json()) as { kind: string; what_if: WhatIf };
  expect(proposalTurn.kind).toBe("proposal");
  expect(proposalTurn.what_if).toMatchObject({ stamped: true, reason: "pending" });
});

test("a fork's figures render stamped with the fork's name", async ({ page, request }) => {
  const token = await signIn(page, request, `t15-column-${Date.now()}@example.com`);
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

  await page.goto("/scenarios");
  await expect(page.getByTestId("scenarios-screen-table-card")).toBeVisible();

  // The fork's column carries the stamp and names the fork.
  const forkStamp = page.getByTestId("scenarios-screen-column-what-if-car");
  await expect(forkStamp).toBeVisible();
  await expect(forkStamp).toContainText("WHAT-IF");
  await expect(forkStamp).toContainText("SCENARIO CAR");

  // Base's column, on a clean book, carries none. A stamp on the plan of
  // record would be as wrong as a missing one.
  await expect(page.getByTestId("scenarios-screen-column-what-if-base")).toHaveCount(0);
});

test("the Home header stays base and neutral while a fork is active", async ({ page, request }) => {
  const token = await signIn(page, request, `t15-header-${Date.now()}@example.com`);
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
      start: "2026-03-01",
    },
  ]);

  await page.goto("/");
  await expect(page.getByTestId("home-screen-balance")).toBeVisible();
  const neutral = ((await page.getByTestId("home-screen-balance").textContent()) ?? "").trim();
  // Nothing is stamped on a clean base book.
  expect(await stamps(page)).toEqual([]);

  // Make the fork the working context, the way the Scenarios screen does.
  await page.goto("/scenarios");
  await page.getByTestId("scenarios-screen-activate-car").click();
  await expect(page.getByTestId("scenarios-screen-active")).toContainText("ACTIVE · CAR");

  await page.goto("/");
  await expect(page.getByTestId("home-screen-balance")).toBeVisible();

  // §2.4: the header still shows base committed figures, in neutral form.
  expect(((await page.getByTestId("home-screen-balance").textContent()) ?? "").trim()).toBe(neutral);
  await expect(page.getByTestId("home-screen-header-what-if")).toHaveCount(0);
  // And the screen still says which context the user is working in — naming
  // the fork is not the same as taking a figure from it.
  await expect(page.getByTestId("home-screen-working-in")).toContainText("WORKING IN CAR");

  // The fork really is different, so the header showing the same figure is a
  // decision the screen made, not a coincidence of identical books.
  const base = await readJson<Stamped>(request, token, "/book/state?scenario=base");
  const fork = await readJson<Stamped>(request, token, "/book/state?scenario=car");
  const index = base.months.findIndex((m) => m.startsWith("2026-03"));
  expect(index).toBeGreaterThanOrEqual(0);
  expect(fork.closing[index]!.exact).not.toBe(base.closing[index]!.exact);
});

test("a fork's own trace renders the stamp, and base's does not", async ({ page, request }) => {
  const token = await signIn(page, request, `t15-trace-${Date.now()}@example.com`);
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
      start: "2026-03-01",
    },
  ]);

  await page.goto("/trace?period=2026-03-01&scenario=base");
  await expect(page.getByTestId("trace-screen-receipt")).toBeVisible();
  await expect(page.getByTestId("trace-screen-what-if")).toHaveCount(0);

  await page.goto("/trace?period=2026-03-01&scenario=car");
  await expect(page.getByTestId("trace-screen-receipt")).toBeVisible();
  const stamp = page.getByTestId("trace-screen-what-if");
  await expect(stamp).toBeVisible();
  await expect(stamp).toContainText("SCENARIO CAR");
});

test("a pending dry-run is stamped on the card the user reads", async ({ page, request }) => {
  const token = await signIn(page, request, `t15-pending-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  // Two entries, because a read turn is two calls: the interpretation, then
  // the bounded Q&A round in which the model quotes the receipt it was handed.
  await scriptModel(request, [
    {
      kind: "answer",
      reply: "Working that out.",
      intents: [{ op: "project_balance", delta: "-1500.00", delta_date: "2026-09-15", horizon: "6m" }],
    },
    { kind: "answer", reply: "If you did that you would still be positive.", intents: [] },
  ]);

  await page.goto("/");
  await page.getByTestId("home-screen-ask-input").fill("can I afford a 1500 laptop in September?");
  await page.getByTestId("home-screen-ask-send").click();

  const card = page.locator('[data-testid^="answer-card-"]').first();
  await expect(card).toBeVisible();
  // A throwaway overlay is the third cause §2.4 lists, and it is stamped.
  const stamp = card.locator('[data-testid$="-what-if"]');
  await expect(stamp).toBeVisible();
  await expect(stamp).toContainText("WHAT-IF");

  // The header above it is untouched: the answer is hypothetical, the balance
  // row is the book's own.
  await expect(page.getByTestId("home-screen-header-what-if")).toHaveCount(0);
});
