/**
 * The F4 path in a browser: fork, compare, activate.
 *
 *   scenarios list → name a fork → confirmation card → apply → compare two
 *   columns with a delta → activate the fork
 *
 * Two things are load-bearing here and neither is a UX detail. Creating a fork
 * is a **write**, so it produces a card and nothing exists until the user
 * applies it (ADR-0029, D-MLP-14). And absent is not zero: a period a scenario
 * has no figure for renders a dash, because the engine keeps that distinction
 * and the compare view is where losing it would be most expensive (SPEC §5-F4).
 */
import { expect, test } from "@playwright/test";

import { readJson, seedBook, seedFork, seedItems, signIn } from "./support";

interface ScenarioList {
  active: string;
  scenarios: { id: string; is_base: boolean; is_active: boolean }[];
}

test("a fork is a confirmed write, not a side effect of naming one", async ({ page, request }) => {
  const token = await signIn(page, request, `fork-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);

  await page.goto("/scenarios");
  await expect(page.getByTestId("scenarios-screen")).toBeVisible();
  await expect(page.getByTestId("scenarios-screen-subline")).toContainText("COMPARE · SAME BOOK");
  await expect(page.getByTestId("scenarios-screen-chip-base")).toBeVisible();

  await page.getByTestId("scenarios-screen-new-name").fill("car");
  await page.getByTestId("scenarios-screen-new-create").click();

  const card = page.getByTestId("scenarios-screen-proposal-card");
  await expect(card).toBeVisible();
  await expect(card.getByTestId("scenarios-screen-proposal-card-label")).toContainText(
    "PENDING · FORK_SCENARIO",
  );
  // The card *is* the change: until it is applied the book has one scenario.
  const before = await readJson<ScenarioList>(request, token, "/book/scenarios");
  expect(before.scenarios.map((s) => s.id)).toEqual(["base"]);

  await card.getByTestId("scenarios-screen-proposal-card-apply").click();
  await expect(page.getByTestId("scenarios-screen-resolution")).toContainText("APPLIED");
  await expect(page.getByTestId("scenarios-screen-chip-car")).toBeVisible();

  const after = await readJson<ScenarioList>(request, token, "/book/scenarios");
  expect(after.scenarios.map((s) => s.id).sort()).toEqual(["base", "car"]);
});

test("compare shows both columns, the service's delta, and where they diverge", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `compare-${Date.now()}@example.com`);
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

  // Both columns, per period, plus the delta column SPEC §5-F4 asks for.
  await expect(page.getByTestId("scenarios-screen-row-2026-06-base")).toBeVisible();
  await expect(page.getByTestId("scenarios-screen-row-2026-06-car")).toBeVisible();
  const delta = page.getByTestId("scenarios-screen-row-2026-06-delta");
  await expect(delta).toBeVisible();

  // Every figure in that row is a string the service sent, and the delta is
  // the service's own — the client subtracts nothing.
  interface Compare {
    periods: { period_start: string; values: Record<string, { display: string } | null>; delta: { display: string } | null }[];
  }
  const compare = await readJson<Compare>(
    request,
    token,
    "/book/compare?scenarios=base,car&metric=cash",
  );
  const june = compare.periods.find((p) => p.period_start.startsWith("2026-06"));
  expect(june, "the horizon should reach June").toBeTruthy();
  expect(june!.delta, "two scenarios means a delta column").not.toBeNull();

  // Undo the presentation and recover the digits: the currency symbol, the
  // thin thousands separators and the typographic minus are all rendering.
  const normalize = (text: string) =>
    text.replace(/\u20ac/g, "").replace(/\u2009/g, "").replace(/\u2212/g, "-").trim();
  expect(normalize((await delta.textContent()) ?? "")).toBe(june!.delta!.display);

  // The curves part company where the event lands, and the label says so.
  await expect(page.getByTestId("scenarios-screen-chart-diverge")).toContainText("DIVERGE JUN 2026");
});

test("activating a fork switches the working context and supersedes nothing silently", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `activate-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  await seedFork(request, token, "downside", [
    {
      op: "add_item",
      id: "pay_cut",
      name: "Pay cut",
      direction: "out",
      amount: "-1500.00",
      recurrence: "1m",
      start: "2026-05-01",
    },
  ]);

  await page.goto("/scenarios");
  await expect(page.getByTestId("scenarios-screen-active")).toContainText("ACTIVE · BASE");

  await page.getByTestId("scenarios-screen-activate-downside").click();
  await expect(page.getByTestId("scenarios-screen-active")).toContainText("ACTIVE · DOWNSIDE");

  const list = await readJson<ScenarioList>(request, token, "/book/scenarios");
  expect(list.active).toBe("downside");
});

test("a scenario with no figure for a period shows a dash, never a zero", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `absent-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  await seedFork(request, token, "twin");

  await page.goto("/scenarios");
  await expect(page.getByTestId("scenarios-screen-table-card")).toBeVisible();

  interface Compare {
    scenarios: string[];
    periods: { period_start: string; values: Record<string, { display: string } | null> }[];
  }
  const compare = await readJson<Compare>(
    request,
    token,
    "/book/compare?scenarios=base,twin&metric=cash",
  );

  // Whatever the engine sent, the screen must not turn an absent figure into
  // a zero. Check every rendered cell against the payload it came from.
  for (const period of compare.periods) {
    const key = period.period_start.slice(0, 7);
    for (const id of compare.scenarios) {
      const cell = page.getByTestId(`scenarios-screen-row-${key}-${id}`);
      const rendered = ((await cell.textContent()) ?? "").trim();
      if (period.values[id] == null) {
        expect(rendered, `${key}/${id} is absent in the payload`).toBe("—");
      } else {
        expect(rendered).not.toBe("—");
      }
    }
  }
});
