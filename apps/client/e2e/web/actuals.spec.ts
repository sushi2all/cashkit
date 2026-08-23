/**
 * The F5 path in a browser: record an actual, correct it, move the cutover.
 *
 * The correction test is the one that matters most. ADR-0012 makes a
 * correction append-only and ADR-0013 says what the interface owes that: the
 * original stays visible and struck, the correction is linked, and the note is
 * mandatory. A scar that is only in the database is not a scar — it has to be
 * on the screen, which is what this checks.
 *
 * The record-actual test checks the one thing the **client** owns of SPEC
 * §5-F5: it sets `context: "actuals_record"` on this screen and nowhere else.
 * The rule itself is the service's and is tested by T18; the client must not
 * re-implement it, so what is asserted here is the flag on the wire and the
 * status that came back.
 */
import { expect, test } from "@playwright/test";

import { readJson, scriptModel, seedBook, seedItems, signIn } from "./support";

interface Ledger {
  events: {
    id: string;
    date: string;
    amount: { display: string; exact: string };
    status: string;
    note: string | null;
    corrects: string | null;
  }[];
}

/** Record one actual through the screen's own flow, and return its id. */
async function recordGroceries(
  page: import("@playwright/test").Page,
  request: import("@playwright/test").APIRequestContext,
  token: string,
  amount: string,
): Promise<string> {
  await scriptModel(request, [
    {
      kind: "answer",
      reply: "Recording that.",
      intents: [{ op: "add_event", date: "2026-03-09", amount, note: "groceries" }],
    },
  ]);
  await page.goto("/actuals");
  await expect(page.getByTestId("actuals-screen-recorded-card")).toBeVisible();
  await page.getByTestId("actuals-screen-ask-input").fill(`groceries on the 9th were ${amount}`);
  await page.getByTestId("actuals-screen-ask-send").click();

  const card = page.getByTestId("actuals-screen-proposal-card");
  await expect(card).toBeVisible();
  await card.getByTestId("actuals-screen-proposal-card-apply").click();
  await expect(page.getByTestId("actuals-screen-resolution")).toContainText("APPLIED");

  const ledger = await readJson<Ledger>(request, token, "/book/events?include_voided=true");
  const recorded = ledger.events.find((e) => e.note === "groceries" && e.corrects === null);
  expect(recorded, "the flow should have stored one actual").toBeTruthy();
  return recorded!.id;
}

test("the record-actual flow sets the discriminator, and only this screen does", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `record-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);

  const contexts: (string | undefined)[] = [];
  page.on("request", (req) => {
    if (!req.url().endsWith("/api/turns")) return;
    const body = req.postData();
    contexts.push(body ? (JSON.parse(body) as { context?: string }).context : undefined);
  });

  const id = await recordGroceries(page, request, token, "-96.00");

  // The client's whole responsibility for SPEC §5-F5, on the wire.
  expect(contexts).toEqual(["actuals_record"]);

  // And the service's verdict, which the client did not compute: the date is
  // before the frozen as-of, so it is an actual.
  const ledger = await readJson<Ledger>(request, token, "/book/events?include_voided=true");
  expect(ledger.events.find((e) => e.id === id)!.status).toBe("actual");

  // The same words from Home carry no context, so the same entry stays a
  // forecast. This is the half the client can get wrong on its own.
  await scriptModel(request, [
    {
      kind: "answer",
      reply: "Adding that.",
      intents: [{ op: "add_event", date: "2026-03-09", amount: "-12.00", note: "elsewhere" }],
    },
  ]);
  await page.goto("/");
  await page.getByTestId("home-screen-ask-input").fill("groceries on the 9th were 12");
  await page.getByTestId("home-screen-ask-send").click();
  await expect(page.locator('[data-testid^="proposal-card-"]').first()).toBeVisible();
  expect(contexts).toEqual(["actuals_record", undefined]);
});

test("a missing date is a clarification, and nothing is stored", async ({ page, request }) => {
  const token = await signIn(page, request, `clarify-actual-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);

  await scriptModel(request, [
    // The model gives an amount and no date on the record flow. SPEC §5-F5:
    // that is a clarification, never a guess.
    { kind: "answer", reply: "Recording that.", intents: [{ op: "add_event", amount: "-40.00", note: "coffee" }] },
  ]);
  await page.goto("/actuals");
  await page.getByTestId("actuals-screen-ask-input").fill("I spent 40 on coffee");
  await page.getByTestId("actuals-screen-ask-send").click();

  const card = page.locator('[data-testid^="actuals-screen-answer-card"]').first();
  await expect(card).toBeVisible();
  await expect(card.locator('[data-testid$="-clarification"]')).toContainText("NOTHING WAS CHANGED");

  const ledger = await readJson<Ledger>(request, token, "/book/events?include_voided=true");
  expect(ledger.events.filter((e) => e.note === "coffee")).toEqual([]);
});

test("correcting an actual leaves the scar on the screen", async ({ page, request }) => {
  const token = await signIn(page, request, `scar-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  const original = await recordGroceries(page, request, token, "-96.00");

  await page.goto("/actuals");
  const row = page.getByTestId(`actuals-screen-ledger-row-${original}`);
  await expect(row).toBeVisible();
  await expect(row.getByTestId(`actuals-screen-ledger-row-${original}-amount`)).toContainText("96.00");

  await page.getByTestId(`actuals-screen-ledger-row-${original}-correct`).click();
  const form = page.getByTestId("actuals-screen-correction-form");
  await expect(form).toBeVisible();

  // The note is mandatory: an amount alone does not enable the button, and the
  // screen says why. A correction without a reason is not auditable (ADR-0012).
  await form.getByTestId("actuals-screen-correction-form-amount").fill("-69.00");
  await expect(form.getByTestId("actuals-screen-correction-form-note-required")).toBeVisible();
  await expect(form.getByTestId("actuals-screen-correction-form-submit")).toBeDisabled();

  await form.getByTestId("actuals-screen-correction-form-note").fill("typo when it was entered");
  await expect(form.getByTestId("actuals-screen-correction-form-submit")).toBeEnabled();
  await form.getByTestId("actuals-screen-correction-form-submit").click();

  const card = page.getByTestId("actuals-screen-proposal-card");
  await expect(card).toBeVisible();
  await expect(card.getByTestId("actuals-screen-proposal-card-label")).toContainText(
    "PENDING · CORRECT_ACTUAL",
  );
  // The note is on the card the user confirms, not tucked away.
  await expect(card.locator('[data-testid$="-op-0"]')).toContainText("typo when it was entered");
  await card.getByTestId("actuals-screen-proposal-card-apply").click();
  await expect(page.getByTestId("actuals-screen-resolution")).toContainText("APPLIED");

  // The scar, on the screen. The original is still there, struck, and says
  // what replaced it; the correction says what it replaced and why.
  const ledger = await readJson<Ledger>(request, token, "/book/events?include_voided=true");
  const correction = ledger.events.find((e) => e.corrects === original);
  expect(correction, "a correction appends a row, it does not edit one").toBeTruthy();

  const originalRow = page.getByTestId(`actuals-screen-ledger-row-${original}`);
  await expect(originalRow).toBeVisible();
  await expect(originalRow.getByTestId(`actuals-screen-ledger-row-${original}-amount`)).toContainText(
    "96.00",
  );
  await expect(
    originalRow.getByTestId(`actuals-screen-ledger-row-${original}-superseded`),
  ).toContainText("CORRECTED");

  const annotation = page.getByTestId(`actuals-screen-ledger-row-${correction!.id}-correction`);
  await expect(annotation).toBeVisible();
  await expect(annotation).toContainText("was");
  await expect(annotation).toContainText("96.00");
  await expect(annotation).toContainText("note: typo when it was entered");

  // The original figure is still struck through in the DOM, not merely faint.
  const struck = await originalRow
    .getByTestId(`actuals-screen-ledger-row-${original}-amount`)
    .evaluate((node) => getComputedStyle(node).textDecorationLine);
  expect(struck).toContain("line-through");
});

test("the cutover is offered as a proposal, never moved by looking at it", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `cutover-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  await recordGroceries(page, request, token, "-96.00");

  await page.goto("/actuals");
  const card = page.getByTestId("actuals-screen-cutover-card");
  await expect(card).toBeVisible();

  interface State {
    book: { cutover: string };
  }
  const before = await readJson<State>(request, token, "/book/state?scenario=base");

  await page.getByTestId("actuals-screen-cutover-apply").click();
  const proposal = page.getByTestId("actuals-screen-proposal-card");
  await expect(proposal).toBeVisible();
  await expect(proposal.getByTestId("actuals-screen-proposal-card-label")).toContainText(
    "PENDING · SET_CUTOVER",
  );

  // Nothing moved on the offer alone (ADR-0029).
  const midway = await readJson<State>(request, token, "/book/state?scenario=base");
  expect(midway.book.cutover).toBe(before.book.cutover);

  await proposal.getByTestId("actuals-screen-proposal-card-apply").click();
  await expect(page.getByTestId("actuals-screen-resolution")).toContainText("APPLIED");
  const after = await readJson<State>(request, token, "/book/state?scenario=base");
  expect(after.book.cutover).not.toBe(before.book.cutover);
});
