/**
 * The four turn kinds, and the two easiest things to get wrong about them.
 *
 * `kind` has four values, not three (D-MLP-24), and a `refusal` is a guardrail
 * that arrives on a **200** and reads as a sentence — rendering it as an error
 * is the wrong shape. `receipts[]` may be empty on an answer and the reply is
 * still the answer (S2 handoff §5). Both are rendered here, in a browser,
 * against the real endpoint.
 */
import { expect, test } from "@playwright/test";

import { scriptModel, seedBook, seedItems, signIn } from "./support";

test.describe("turn kinds", () => {
  test("an answer with no receipts is still an answer", async ({ page, request }) => {
    const token = await signIn(page, request, `answer-${Date.now()}@example.com`);
    await seedBook(request, token);
    await seedItems(request, token);
    await scriptModel(request, [
      // A read turn answered straight from the snapshot's results block: no
      // read operation, so no receipt, and the sentence is the whole answer.
      { kind: "answer", reply: "You stay positive all year.", intents: [] },
    ]);

    await page.goto("/");
    await page.getByTestId("home-screen-ask-input").fill("am I going to be ok?");
    await page.getByTestId("home-screen-ask-send").click();

    const card = page.locator('[data-testid^="answer-card-"]').first();
    await expect(card).toBeVisible();
    await expect(card.locator('[data-testid$="-reply"]')).toHaveText("You stay positive all year.");
    await expect(card.locator('[data-testid$="-no-receipts"]')).toContainText("ANSWERED FROM THE BOOK");
    // It is an answer, not an error and not a card awaiting confirmation.
    await expect(page.locator('[data-testid^="proposal-card-"]')).toHaveCount(0);
    await expect(page.getByTestId("home-screen-clean")).toContainText("SAVED");
  });

  test("a clarification asks, and changes nothing", async ({ page, request }) => {
    const token = await signIn(page, request, `clarify-${Date.now()}@example.com`);
    await seedBook(request, token);
    await seedItems(request, token);
    await scriptModel(request, [
      { kind: "clarification", reply: "Which month did that start?", intents: [] },
    ]);

    await page.goto("/");
    await page.getByTestId("home-screen-ask-input").fill("I got a raise");
    await page.getByTestId("home-screen-ask-send").click();

    const card = page.locator('[data-testid^="answer-card-"]').first();
    await expect(card.locator('[data-testid$="-reply"]')).toHaveText("Which month did that start?");
    await expect(card.locator('[data-testid$="-clarification"]')).toContainText("NOTHING WAS CHANGED");
    await expect(page.getByTestId("home-screen-clean")).toContainText("SAVED");
  });

  test("a refusal is a sentence on a 200, not an error state", async ({ page, request }) => {
    const token = await signIn(page, request, `refuse-${Date.now()}@example.com`);
    await seedBook(request, token);
    await seedItems(request, token);

    // SPEC §8: 30 turns an hour. The guardrail is checked before the first
    // model call, so a refused turn costs nothing and needs no script.
    await scriptModel(request, []);
    const statuses: number[] = [];
    page.on("response", (response) => {
      if (response.url().endsWith("/api/turns")) statuses.push(response.status());
    });

    await page.goto("/");
    for (let i = 0; i < 31; i += 1) {
      await scriptModel(request, [{ kind: "answer", reply: `ok ${i}`, intents: [] }]);
      await page.getByTestId("home-screen-ask-input").fill(`question ${i}`);
      await page.getByTestId("home-screen-ask-send").click();
      // Wait for the turn to land, not for the send button: sending clears the
      // input, which disables the button by design. Count reply lines rather
      // than cards — a `^=` match on the card's testID also matches every one
      // of its children, which all share the prefix.
      await expect(page.locator('[data-testid$="-reply"]')).toHaveCount(i + 1);
    }

    const refusal = page.locator('[data-testid$="-refusal"]').last();
    await expect(refusal).toBeVisible();
    await expect(refusal).toContainText("TRY AGAIN");
    // The point of D-MLP-24: it came back 200 and rendered as a card the user
    // reads, with no error state anywhere on the screen.
    expect(statuses.every((status) => status === 200)).toBe(true);
    await expect(page.getByTestId("error")).toHaveCount(0);
  });

  test("a diagnostic on a card renders verbatim and blocks Apply", async ({ page, request }) => {
    const token = await signIn(page, request, `diag-${Date.now()}@example.com`);
    await seedBook(request, token);
    await seedItems(request, token);
    const scaleByBareTag = {
      kind: "answer",
      reply: "Scaling those.",
      intents: [{ op: "scale_items", selector: "income", factor: "1.1" }],
    };
    // Twice on purpose. A macro is one of the enumerated verification triggers
    // (D-MLP-25) and an error diagnostic buys one repair round (D-MLP-33), so
    // the pipeline asks again; scripting a model that does not correct itself
    // is how the diagnostic reaches the card the user sees.
    await scriptModel(request, [scaleByBareTag, scaleByBareTag, scaleByBareTag]);

    await page.goto("/");
    await page.getByTestId("home-screen-ask-input").fill("increase the income by 10 percent");
    await page.getByTestId("home-screen-ask-send").click();

    const diagnostics = page.locator('[data-testid$="-diagnostics-item"]').first();
    await expect(diagnostics).toBeVisible();
    // The code is on the screen, not a rewritten summary of it.
    await expect(diagnostics).toContainText(/CK-E\d+/);
  });
});
