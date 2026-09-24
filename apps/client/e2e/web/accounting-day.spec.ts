/**
 * The accounting day (SPEC §6-S15, migration 0003).
 *
 * A setting, not a proposal: saving it produces no card, and it changes the day
 * a line the user authors *next* falls on when they name no day.
 */
import { expect, test } from "@playwright/test";

import { scriptModel, seedBook, signIn } from "./support";

test("the day is saved from Settings and shown back", async ({ page, request }) => {
  const token = await signIn(page, request, "day@example.com");
  await seedBook(request, token);

  await page.goto("/settings");
  await expect(page.getByTestId("settings-screen-accounting-day")).toBeVisible();
  await page.getByTestId("settings-screen-accounting-day").fill("27");
  await page.getByTestId("settings-screen-accounting-day-submit").click();
  await expect(page.getByTestId("settings-screen-accounting-day-note")).toContainText("DAY 27");

  // No card: a preference is not a change to the book's figures.
  await expect(page.locator('[data-testid^="proposal-card"]')).toHaveCount(0);

  await page.reload();
  await expect(page.getByTestId("settings-screen-accounting-day")).toHaveValue("27");
});

test("a day outside 1–28 is refused before it is sent", async ({ page, request }) => {
  const token = await signIn(page, request, "day2@example.com");
  await seedBook(request, token);

  await page.goto("/settings");
  await page.getByTestId("settings-screen-accounting-day").fill("31");
  await page.getByTestId("settings-screen-accounting-day-submit").click();
  await expect(page.getByTestId("settings-screen-accounting-day-note")).toContainText("1 TO 28");
});

test("a turn that names no day lands on the book's day", async ({ page, request }) => {
  const token = await signIn(page, request, "day3@example.com");
  await seedBook(request, token);

  await page.goto("/settings");
  await page.getByTestId("settings-screen-accounting-day").fill("27");
  await page.getByTestId("settings-screen-accounting-day-submit").click();
  await expect(page.getByTestId("settings-screen-accounting-day-note")).toContainText("DAY 27");

  // The model writes the month alone — it was told no day.
  await scriptModel(request, [
    {
      kind: "answer",
      reply: "Rent, monthly from April.",
      intents: [
        {
          op: "add_item",
          id: "rent",
          direction: "out",
          amount: "-950.00",
          recurrence: "1m",
          start: "2026-04",
        },
      ],
    },
  ]);

  await page.goto("/");
  await page.getByTestId("home-screen-ask-input").fill("rent is 950 a month from April");
  await page.getByTestId("home-screen-ask-send").click();

  const card = page.locator('[data-testid^="proposal-card-"]').first();
  await expect(card).toBeVisible();
  await expect(card).toContainText("27 APR 2026");
});

test("end of month is an option, and it is the engine's anchor", async ({ page, request }) => {
  const token = await signIn(page, request, "eom@example.com");
  await seedBook(request, token);

  await page.goto("/settings");
  await page.getByTestId("settings-screen-accounting-day-eom").click();
  await expect(page.getByTestId("settings-screen-accounting-day-note")).toContainText(
    "LAST DAY OF EACH MONTH",
  );

  await scriptModel(request, [
    {
      kind: "answer",
      reply: "Rent, monthly from April.",
      intents: [
        {
          op: "add_item",
          id: "rent",
          direction: "out",
          amount: "-950.00",
          recurrence: "1m",
          start: "2026-04",
        },
      ],
    },
  ]);

  await page.goto("/");
  await page.getByTestId("home-screen-ask-input").fill("rent is 950 a month from April");
  await page.getByTestId("home-screen-ask-send").click();

  // April has 30 days: the card carries the month's own last day, not a 31st
  // clamped backwards and not the 1st.
  const card = page.locator('[data-testid^="proposal-card-"]').first();
  await expect(card).toBeVisible();
  await expect(card).toContainText("30 APR 2026");
});
