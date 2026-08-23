/**
 * The guards, seen from the browser.
 *
 * These are the client-side halves of invariants the service already enforces.
 * The service being right is tested in `apps/service/trials`; what is tested
 * here is that the *interface* does not undo it — that it never applies a
 * proposal itself, never retries a stale one silently, and shows an expired
 * sign-in link as the error state SPEC §6-S12 asks for.
 */
import { expect, test } from "@playwright/test";

import { GYM_INTENT, scriptModel, seedBook, seedItems, signIn } from "./support";

test("a card the book has moved past is not applied, and is not retried silently", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `stale-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  await scriptModel(request, [
    { kind: "answer", reply: "I will add a gym membership.", intents: [GYM_INTENT] },
  ]);

  await page.goto("/");
  await page.getByTestId("home-screen-ask-input").fill("I joined a gym, 49.90 a month from April");
  await page.getByTestId("home-screen-ask-send").click();

  const card = page.locator('[data-testid^="proposal-card-"]').first();
  await expect(card).toBeVisible();

  // The ground moves under the pending card, from somewhere else entirely.
  // SPEC §2.5: a save supersedes every pending proposal.
  const save = await request.post("/api/book/save", {
    headers: { Authorization: `Bearer ${token}` },
    data: { message: "moved on" },
  });
  expect(save.ok()).toBeTruthy();

  const itemsBefore = await request.get("/api/book/state", {
    headers: { Authorization: `Bearer ${token}` },
  });
  const before = ((await itemsBefore.json()) as { items: { id: string }[] }).items.map((i) => i.id);
  expect(before).not.toContain("gym");

  await card.locator('[data-testid$="-apply"]').click();

  // The user is told. The UI does not re-post, and it does not pretend the
  // change landed.
  const failure = page.locator('[data-testid^="failure-"][data-testid$="-message"]').last();
  await expect(failure).toContainText(/superseded|moved|already/i);

  const after = await request.get("/api/book/state", { headers: { Authorization: `Bearer ${token}` } });
  const afterItems = ((await after.json()) as { items: { id: string }[] }).items.map((i) => i.id);
  expect(afterItems, "nothing was applied").not.toContain("gym");
  expect(afterItems).toEqual(before);
});

test("discarding a card leaves the book alone", async ({ page, request }) => {
  const token = await signIn(page, request, `discard-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  await scriptModel(request, [
    { kind: "answer", reply: "I will add a gym membership.", intents: [GYM_INTENT] },
  ]);

  await page.goto("/");
  await page.getByTestId("home-screen-ask-input").fill("I joined a gym, 49.90 a month from April");
  await page.getByTestId("home-screen-ask-send").click();

  const card = page.locator('[data-testid^="proposal-card-"]').first();
  await expect(card).toBeVisible();
  await card.locator('[data-testid$="-discard"]').click();

  await expect(page.locator('[data-testid^="resolution-"]').first()).toContainText("DISCARDED");
  await expect(page.getByTestId("home-screen-clean")).toContainText("SAVED");

  const state = await request.get("/api/book/state", {
    headers: { Authorization: `Bearer ${token}` },
  });
  const items = ((await state.json()) as { items: { id: string }[] }).items.map((i) => i.id);
  expect(items).not.toContain("gym");
});

test("an expired or reused sign-in link shows the error state, not a blank screen", async ({
  page,
  request,
}) => {
  const email = `expired-${Date.now()}@example.com`;
  await signIn(page, request, email);

  // A link token is single-use (SPEC §3). Opening the same link again is the
  // most common way a user meets this state — mail clients prefetch.
  const linkResponse = await request.get(`/__control/link?email=${encodeURIComponent(email)}`);
  const { url } = (await linkResponse.json()) as { url: string };

  await page.evaluate(() => window.localStorage.clear());
  await page.goto(url);

  await expect(page.getByTestId("verify-error")).toBeVisible();
  await expect(page.getByTestId("verify-error-state-message")).toContainText(
    /expired|already been used|did not work/i,
  );
  await page.getByTestId("verify-error-state-retry").click();
  await expect(page.getByTestId("auth-screen-email")).toBeVisible();
});
