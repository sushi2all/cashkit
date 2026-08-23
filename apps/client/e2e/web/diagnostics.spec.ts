/**
 * R10 — `validate()` diagnostics render verbatim.
 *
 * ADR-0015 and PROMPT non-negotiable 5: diagnostics are never rewritten, never
 * summarized, never suppressed, and never turned into advice. That is the rule
 * the product's credibility rests on — an engine whose findings the interface
 * edits is an engine you cannot quote.
 *
 * So every assertion here compares against the **live endpoint**, not a fixture
 * anyone typed: the test reads `GET /book/validate`, then requires every field
 * of every diagnostic to appear on the screen, character for character. A
 * message the client shortened by a word fails. A diagnostic the client decided
 * was uninteresting fails. A suggested fix reworded into encouragement fails.
 *
 * `validate()` checks model consistency, not domain completeness (ADR-0021),
 * and the consumer MLP defers the domain-coverage duty entirely (D-MLP-02).
 * Nothing here grades a book.
 */
import { expect, test, type APIRequestContext } from "@playwright/test";

import { readJson, seedBook, seedItems, signIn } from "./support";

interface Diagnostic {
  code: string;
  severity: string;
  message: string;
  suggested_fix: string;
  item_id: string | null;
  field: string | null;
}
interface Validate {
  diagnostics: Diagnostic[];
}

/**
 * Give the engine something true to say.
 *
 * An actual dated on or after the book's cutover is `CK-W003` — a real
 * catalogue warning about the book and the ledger together, with a real
 * message and a real suggested fix. It is authored through the ordinary
 * proposal pipeline, so the fixture obeys ADR-0029 like everything else.
 */
async function recordAnActual(request: APIRequestContext, token: string): Promise<void> {
  const auth = { Authorization: `Bearer ${token}` };
  const created = await request.post("/api/book/edits", {
    headers: auth,
    data: {
      origin: "cell_edit",
      context: "actuals_record",
      ops: [{ op: "record_actual", date: "2026-03-09", amount: "-96.00", note: "groceries" }],
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

test("every field of every validate() diagnostic is on the screen, character for character", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `r10-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  await recordAnActual(request, token);

  const validate = await readJson<Validate>(request, token, "/book/validate");
  // The gate is only meaningful if the engine actually said something.
  expect(validate.diagnostics.length, "the fixture should produce a diagnostic").toBeGreaterThan(0);

  await page.goto("/actuals");
  const card = page.getByTestId("actuals-screen-diagnostics-card");
  await expect(card).toBeVisible();
  await expect(page.getByTestId("actuals-screen-diagnostics")).toBeVisible();

  const rendered = (await card.innerText()).replace(/\s+/g, " ");
  const squash = (text: string) => text.replace(/\s+/g, " ").trim();

  for (const diagnostic of validate.diagnostics) {
    expect(rendered, `code ${diagnostic.code} is missing from the screen`).toContain(diagnostic.code);
    expect(rendered, `severity of ${diagnostic.code} is missing`).toContain(
      diagnostic.severity.toUpperCase(),
    );
    // The whole message, word for word. This is the assertion the gate names.
    expect(rendered, `the message of ${diagnostic.code} was not rendered verbatim`).toContain(
      squash(diagnostic.message),
    );
    expect(rendered, `the suggested fix of ${diagnostic.code} was dropped`).toContain(
      squash(diagnostic.suggested_fix),
    );
    if (diagnostic.item_id) expect(rendered).toContain(diagnostic.item_id);
    if (diagnostic.field) expect(rendered).toContain(diagnostic.field);
  }

  // As many rendered entries as the endpoint sent: none filtered, none merged.
  await expect(page.locator('[data-testid="actuals-screen-diagnostics-item"]')).toHaveCount(
    validate.diagnostics.length,
  );
});

test("a book the engine is happy with is reported as silence, not as a grade", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `r10-clean-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);

  const validate = await readJson<Validate>(request, token, "/book/validate");
  expect(validate.diagnostics).toEqual([]);

  await page.goto("/actuals");
  await expect(page.getByTestId("actuals-screen-diagnostics-none")).toBeVisible();
  // No score, no "healthy", no advice (ADR-0015, D-MLP-02).
  const rendered = (await page.getByTestId("actuals-screen-diagnostics-card").innerText()).toLowerCase();
  for (const word of ["healthy", "looks good", "score", "you should", "we recommend"]) {
    expect(rendered, `"${word}" is advice, and R10 does not give advice`).not.toContain(word);
  }
});

test("an amount the money path cannot take is refused before it is sent", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `r10-shape-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);
  await recordAnActual(request, token);

  // Nothing may leave the screen while the amount is not a decimal string.
  const posted: string[] = [];
  page.on("request", (req) => {
    if (req.url().endsWith("/api/book/edits")) posted.push(req.url());
  });

  await page.goto("/actuals");
  const link = page.locator('[data-testid^="actuals-screen-ledger-row-"][data-testid$="-correct"]');
  await expect(link.first()).toBeVisible();
  await link.first().click();

  const form = page.getByTestId("actuals-screen-correction-form");
  await form.getByTestId("actuals-screen-correction-form-amount").fill("about ninety");
  await form.getByTestId("actuals-screen-correction-form-note").fill("guessing");

  // The schema takes money as a decimal string so no float enters the money
  // path; a string that is not one comes back a 422, which is a transport
  // error where the user needs guidance. The form says so instead.
  await expect(form.getByTestId("actuals-screen-correction-form-amount-shape")).toBeVisible();
  await expect(form.getByTestId("actuals-screen-correction-form-submit")).toBeDisabled();
  expect(posted, "nothing should have been sent").toEqual([]);

  // A decimal string goes through, unchanged, exactly as it was typed.
  await form.getByTestId("actuals-screen-correction-form-amount").fill("-69.00");
  await expect(form.getByTestId("actuals-screen-correction-form-submit")).toBeEnabled();
  await form.getByTestId("actuals-screen-correction-form-submit").click();
  await expect(page.getByTestId("actuals-screen-proposal-card")).toBeVisible();
  expect(posted.length).toBe(1);
});
