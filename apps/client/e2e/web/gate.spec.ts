/**
 * The S3 gate path, in a browser:
 *
 *   auth → book (API-seeded) → mutation turn → apply proposal → forecast →
 *   trace → save
 *
 * Every step goes through the real service. The only stand-in anywhere is the
 * model provider, and the assertions are about what the *client* does with
 * what the service returned — that the card is shown before anything changes,
 * that applying is a separate act, and that the figures on screen are the
 * service's own strings.
 */
import { expect, test } from "@playwright/test";

import { GYM_INTENT, scriptModel, seedBook, seedItems, signIn } from "./support";

test("auth → book → mutation turn → apply → forecast → trace → save", async ({ page, request }) => {
  const email = `gate-${Date.now()}@example.com`;

  // --- auth ---------------------------------------------------------------
  const token = await signIn(page, request, email);

  // --- book ---------------------------------------------------------------
  await seedBook(request, token);
  await seedItems(request, token);
  await page.goto("/");

  const home = page.getByTestId("home-screen");
  await expect(home).toBeVisible();
  await expect(page.getByTestId("home-screen-eyebrow")).toContainText("AS-OF");
  // Provenance is an element on every screen with computed figures (SPEC §6).
  await expect(page.getByTestId("home-screen-balance")).toBeVisible();
  await expect(page.getByTestId("home-screen-as-of")).toContainText("SCENARIO BASE");
  await expect(page.getByTestId("home-screen-clean")).toContainText("SAVED");

  const balanceBefore = await page.getByTestId("home-screen-balance").textContent();

  // --- mutation turn ------------------------------------------------------
  await scriptModel(request, [
    { kind: "answer", reply: "I will add a gym membership.", intents: [GYM_INTENT] },
  ]);

  await page.getByTestId("home-screen-ask-input").fill("I joined a gym, 49.90 a month from April");
  await page.getByTestId("home-screen-ask-send").click();

  // The user's own words render as a quote, not as a bubble (ADR-0023).
  await expect(page.getByText("I joined a gym, 49.90 a month from April")).toBeVisible();

  const card = page.locator('[data-testid^="proposal-card-"]').first();
  await expect(card).toBeVisible();
  await expect(card.locator('[data-testid$="-label"]')).toContainText("PENDING · ADD_ITEM");
  // The typed, human-readable form of the change (SPEC §5-F2).
  await expect(card.locator('[data-testid$="-op-0"]')).toContainText("Add expense");
  // The dry-run deltas block: before → after, both computed by the service.
  await expect(card.getByTestId("deltas")).toBeVisible();
  await expect(card.getByTestId("delta-closing")).toBeVisible();
  await expect(card.getByTestId("delta-min-cash")).toBeVisible();

  // Nothing has changed yet: the card *is* the change, and the header still
  // shows committed state (ADR-0029, ADR-0024).
  await expect(page.getByTestId("home-screen-balance")).toHaveText(balanceBefore ?? "");
  await expect(page.getByTestId("home-screen-clean")).toContainText("SAVED");

  // --- apply the proposal -------------------------------------------------
  await card.locator('[data-testid$="-apply"]').click();

  await expect(page.locator('[data-testid^="resolution-"]').first()).toContainText("APPLIED");
  // Applying wrote into the working overlay, so the book is now dirty and the
  // Save affordance appears (SPEC §2.4, §6 shared inventory).
  await expect(page.getByTestId("home-screen-dirty-flag")).toContainText("UNSAVED CHANGES");

  // --- forecast -----------------------------------------------------------
  await page.getByTestId("home-screen-forecast-link").click();

  const forecast = page.getByTestId("forecast-screen");
  await expect(forecast).toBeVisible();
  await expect(page.getByTestId("forecast-screen-subline")).toContainText("AS-OF");
  await expect(page.getByTestId("forecast-screen-chart")).toBeVisible();
  await expect(page.getByTestId("forecast-screen-footer-note")).toContainText("ALL FIGURES COMPUTED");
  await expect(page.getByTestId("forecast-screen-provenance")).toContainText("ENGINE");

  const aprilRow = page.getByTestId("forecast-screen-row-2026-04");
  await expect(aprilRow).toBeVisible();

  // --- trace --------------------------------------------------------------
  await aprilRow.click();

  const trace = page.getByTestId("trace-screen");
  await expect(trace).toBeVisible();
  await expect(page.getByTestId("trace-screen-question")).toContainText("Why");
  await expect(page.getByTestId("trace-screen-receipt")).toBeVisible();
  await expect(page.getByTestId("trace-screen-opening")).toBeVisible();
  await expect(page.getByTestId("trace-screen-total")).toBeVisible();
  // The applied change is on the receipt, by item id.
  await expect(page.getByTestId("trace-screen-row-gym")).toContainText("item:gym");

  // The engine panel — the screen's whole point (SPEC §6-S5).
  const panel = page.getByTestId("trace-screen-engine-panel");
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("engine-engine")).toContainText("DETERMINISTIC");
  await expect(page.getByTestId("engine-rounding")).toContainText("CANONICAL ORDER · 4DP");
  await expect(page.getByTestId("engine-book-revision")).toBeVisible();

  // Row level: the engine's own steps, and full precision where it belongs.
  await page.getByTestId("trace-screen-row-gym").click();
  await expect(page.getByTestId("trace-screen-detail")).toBeVisible();
  await expect(page.getByTestId("trace-screen-detail-exact")).toContainText("EXACT");

  await page.getByTestId("trace-screen-reproduce").click();
  await expect(page.getByTestId("trace-screen-reproduce-verdict")).toContainText("REPRODUCED");

  // --- save ---------------------------------------------------------------
  await page.goto("/");
  await expect(page.getByTestId("home-screen-dirty-flag")).toContainText("UNSAVED CHANGES");
  await page.getByTestId("home-screen-save").click();

  await expect(page.getByTestId("home-screen-clean")).toContainText("SAVED · REV");
  await expect(page.getByTestId("home-screen-dirty")).toHaveCount(0);
});
