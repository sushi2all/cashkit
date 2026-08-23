/**
 * Shared helpers for the web E2E: signing in through the magic link, seeding a
 * book through the API, and scripting the model provider.
 */
import { expect, type APIRequestContext, type Page } from "@playwright/test";

/** The change turn the gate drives. Matches the service suite's own fixture. */
export const GYM_INTENT = {
  op: "add_item",
  id: "gym",
  direction: "out",
  amount: "-49.90",
  recurrence: "1m",
  start: "2026-04-01",
};

export async function scriptModel(request: APIRequestContext, responses: unknown[]): Promise<void> {
  const response = await request.post("/__control/script", { data: { responses, replace: true } });
  expect(response.ok()).toBeTruthy();
}

/**
 * Sign in the way a user does: ask for a link on the Auth screen, then open the
 * link. The token is read from the harness's mailer — the service never
 * returns one in any response, in any mode.
 */
export async function signIn(page: Page, request: APIRequestContext, email: string): Promise<string> {
  await page.goto("/auth");
  await page.getByTestId("auth-screen-email").fill(email);
  await page.getByTestId("auth-screen-submit").click();
  await expect(page.getByTestId("auth-screen-sent")).toBeVisible();

  const linkResponse = await request.get(`/__control/link?email=${encodeURIComponent(email)}`);
  expect(linkResponse.ok()).toBeTruthy();
  const { url } = (await linkResponse.json()) as { url: string };

  await page.goto(url);
  // The bearer lands in the platform token store; on web that is localStorage.
  await expect
    .poll(async () => page.evaluate(() => window.localStorage.getItem("cashkit.session")), {
      timeout: 20_000,
    })
    .not.toBeNull();

  const raw = await page.evaluate(() => window.localStorage.getItem("cashkit.session"));
  return (JSON.parse(raw as string) as { token: string }).token;
}

/**
 * Seed the book through `POST /books`.
 *
 * The gate permits this: "book (API-seeded via POST /books is acceptable here;
 * the UI path is session S5's gate)". The horizon brackets the harness's frozen
 * clock, so `as_of` falls inside it and "today" means something.
 */
export async function seedBook(request: APIRequestContext, token: string): Promise<void> {
  const response = await request.post("/api/books", {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      horizon_start: "2026-01-01",
      horizon_end: "2027-01-01",
      opening_balance: "2500.00",
    },
  });
  expect(response.status(), await response.text()).toBe(201);
}

/** A few real items, so the forecast and the trace have something to explain. */
export async function seedItems(request: APIRequestContext, token: string): Promise<void> {
  const auth = { Authorization: `Bearer ${token}` };
  const create = await request.post("/api/book/edits", {
    headers: auth,
    data: {
      origin: "onboarding",
      ops: [
        {
          op: "add_item",
          id: "salary",
          name: "Salary",
          direction: "in",
          amount: "2617.33",
          recurrence: "1m",
          start: "2026-01-01",
        },
        {
          op: "add_item",
          id: "rent",
          name: "Rent",
          direction: "out",
          amount: "-912.50",
          recurrence: "1m",
          start: "2026-01-01",
        },
      ],
    },
  });
  expect([200, 201], await create.text()).toContain(create.status());
  const body = (await create.json()) as { kind: string; proposal: { id: string } };
  expect(body.kind).toBe("proposal");

  // Nothing lands without an accepted proposal — not even a fixture (ADR-0029).
  const accept = await request.post(`/api/proposals/${body.proposal.id}`, {
    headers: auth,
    data: { action: "accept" },
  });
  expect(accept.status(), await accept.text()).toBe(200);
  expect(((await accept.json()) as { kind: string }).kind).toBe("applied");

  const save = await request.post("/api/book/save", {
    headers: auth,
    data: { message: "seed" },
  });
  expect(save.status(), await save.text()).toBe(200);
}

/**
 * Create a fork through the ordinary proposal pipeline and apply it.
 *
 * Through `POST /book/edits` and `POST /proposals/{id}` — a fork is a write and
 * ADR-0029 admits no exception for one that looks harmless (D-MLP-14). The
 * fixture obeys the invariant it is setting up a test for.
 */
export async function seedFork(
  request: APIRequestContext,
  token: string,
  name: string,
  ops: Record<string, unknown>[] = [],
): Promise<void> {
  const auth = { Authorization: `Bearer ${token}` };
  const create = await request.post("/api/book/edits", {
    headers: auth,
    data: { origin: "button", ops: [{ op: "fork_scenario", name, note: "seeded" }] },
  });
  expect([200, 201], await create.text()).toContain(create.status());
  const proposal = ((await create.json()) as { proposal: { id: string } }).proposal;
  const accept = await request.post(`/api/proposals/${proposal.id}`, {
    headers: auth,
    data: { action: "accept" },
  });
  expect(((await accept.json()) as { kind: string }).kind).toBe("applied");

  if (ops.length > 0) {
    const change = await request.post("/api/book/edits", {
      headers: auth,
      data: { origin: "button", scenario: name, ops },
    });
    expect([200, 201], await change.text()).toContain(change.status());
    const payload = (await change.json()) as { kind: string; proposal?: { id: string; diagnostics: unknown[] } };
    expect(JSON.stringify(payload)).toContain('"kind":"proposal"');
    const second = payload.proposal!;
    expect(JSON.stringify(second.diagnostics), "the fixture change must be clean").toBe("[]");
    const applied = await request.post(`/api/proposals/${second.id}`, {
      headers: auth,
      data: { action: "accept" },
    });
    expect(await applied.text()).toContain('"kind":"applied"');
  }

  const save = await request.post("/api/book/save", {
    headers: auth,
    data: { message: `fork ${name}` },
  });
  expect(save.status(), await save.text()).toBe(200);
}

/** Read one payload straight from the service, to check it against the screen. */
export async function readJson<T>(
  request: APIRequestContext,
  token: string,
  path: string,
): Promise<T> {
  const response = await request.get(`/api${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as T;
}
