/**
 * The Item screens and Settings, in a browser (SPEC §6-S9…S11, S15).
 *
 * What is checked here is the ADR-0013 cell taxonomy arriving at its
 * item-level counterpart: a generated row shows the engine's arithmetic and
 * offers the two real edits (M2 from a date, M5 as a one-off); an actual is
 * not removable and says why; a schedule date is added through a proposal;
 * and every settings change is a card, not a save button.
 */
import { expect, test } from "@playwright/test";

import { readJson, seedBook, seedItems, signIn } from "./support";

interface State {
  book: { horizon_start: string; horizon_end: string; opening_balance: { exact: string } };
  revision: string | null;
}
interface Ledger {
  events: { id: string; status: string; note: string | null }[];
}

test("the item screen shows the engine's own rule, and its edits are proposals", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `item-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);

  await page.goto("/item?id=rent");
  await expect(page.getByTestId("item-screen")).toBeVisible();
  await expect(page.getByTestId("item-screen-title")).toHaveText("Rent");
  await expect(page.getByTestId("item-screen-subline")).toContainText("item:rent");
  await expect(page.getByTestId("item-screen-subline")).toContainText("SCENARIO BASE");

  // The rule, in the engine's words. "recurrence every 1 month" is a phrase
  // the engine produced; the screen quotes it rather than parsing it.
  await expect(page.getByTestId("item-screen-rule-repeats")).toContainText("recurrence every");
  await expect(page.getByTestId("item-screen-rule-starts")).toContainText("segment starting");
  // The arithmetic ADR-0013 requires a generated cell to show.
  await expect(page.getByTestId("item-screen-step-0")).toContainText("segments[");
  // And the honest limit of reading a rule out of traces.
  await expect(page.getByTestId("item-screen-segments-caveat")).toContainText("INSIDE THE HORIZON");

  // Nothing may be confirmed except by the user pressing Apply (ADR-0029), so
  // count confirmations at the network rather than inferring them from the
  // screen — an optimistic client would race a DOM assertion and win.
  const confirmations: string[] = [];
  page.on("request", (req) => {
    if (/\/api\/proposals\//.test(req.url()) && req.method() === "POST") {
      confirmations.push(req.url());
    }
  });
  const traceBefore = await readJson<{ trace: { value: { exact: string } } }>(
    request,
    token,
    "/book/trace?item=rent&period=2026-09-01&measure=cash&scenario=base",
  );

  // M2: change the amount from a date. It splits the segment; it is a card.
  await page.getByTestId("item-screen-change-from").fill("2026-09-01");
  await page.getByTestId("item-screen-change-amount").fill("-1000.00");
  await page.getByTestId("item-screen-change-submit").click();

  const card = page.getByTestId("item-screen-proposal-card");
  await expect(card).toBeVisible();
  await expect(card.getByTestId("item-screen-proposal-card-label")).toContainText(
    "PENDING · SET_AMOUNT",
  );
  await expect(card.locator('[data-testid$="-op-0"]')).toContainText("Change amount");
  await expect(card.getByTestId("deltas")).toBeVisible();

  // The card *is* the change: raising it confirmed nothing and moved nothing.
  expect(confirmations, "no confirmation may be posted before Apply").toEqual([]);
  const midway = await readJson<{ trace: { value: { exact: string } } }>(
    request,
    token,
    "/book/trace?item=rent&period=2026-09-01&measure=cash&scenario=base",
  );
  expect(midway.trace.value.exact, "the pending card must not have applied itself").toBe(
    traceBefore.trace.value.exact,
  );

  await card.getByTestId("item-screen-proposal-card-apply").click();
  await expect(page.getByTestId("item-screen-resolution")).toContainText("APPLIED");

  // The engine now reports two segments, because a change from a date splits
  // rather than rewrites — which is the property M2 exists to preserve.
  await expect(page.getByTestId("item-screen-segment-1")).toBeVisible();

  // September moved and the book did; before Apply, neither had.
  const traceAfter = await readJson<{ trace: { value: { exact: string } } }>(
    request,
    token,
    "/book/trace?item=rent&period=2026-09-01&measure=cash&scenario=base",
  );
  expect(traceAfter.trace.value.exact).not.toBe(traceBefore.trace.value.exact);
  // Exactly one confirmation, and it was the user's.
  expect(confirmations).toHaveLength(1);
});

test("the custom view lists every occurrence and adds a date as a proposal", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `custom-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);

  await page.goto("/item?id=rent");
  await page.getByTestId("item-screen-variant-custom").click();
  await expect(page.getByTestId("item-screen-dates-card")).toBeVisible();
  await expect(page.getByTestId("item-screen-date-2026-03")).toBeVisible();

  // "+ Add a date" is `edit_schedule_date`, a host op, and it is a card. Rent
  // is rule-backed, so the engine refuses it — and the refusal is on the card
  // rather than swallowed.
  await page.getByTestId("item-screen-add-date").fill("2027-01-15");
  await page.getByTestId("item-screen-add-amount").fill("-800.00");
  await page.getByTestId("item-screen-add-submit").click();

  const card = page.getByTestId("item-screen-proposal-card");
  await expect(card).toBeVisible();
  await expect(card.getByTestId("item-screen-proposal-card-label")).toContainText(
    "PENDING · EDIT_SCHEDULE_DATE",
  );
  const diagnostics = card.getByTestId("item-screen-proposal-card-diagnostics");
  await expect(diagnostics).toBeVisible();
  await expect(diagnostics).toContainText(/CK-[EWI]\d{3}/);
  await expect(card.getByTestId("item-screen-proposal-card-apply")).toBeDisabled();
});

test("an actual cannot be removed, and the screen says what to do instead", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `event-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);

  const auth = { Authorization: `Bearer ${token}` };
  const created = await request.post("/api/book/edits", {
    headers: auth,
    data: {
      origin: "cell_edit",
      context: "actuals_record",
      ops: [{ op: "record_actual", date: "2026-03-05", amount: "-1000.00", item: "rent", note: "rent" }],
    },
  });
  const proposal = ((await created.json()) as { proposal: { id: string } }).proposal;
  await request.post(`/api/proposals/${proposal.id}`, { headers: auth, data: { action: "accept" } });

  const ledger = await readJson<Ledger>(request, token, "/book/events?include_voided=true");
  const actual = ledger.events.find((e) => e.status === "actual");
  expect(actual).toBeTruthy();

  await page.goto(`/event?id=${actual!.id}`);
  await expect(page.getByTestId("event-screen-event-card")).toBeVisible();
  await expect(page.getByTestId("event-screen-status-card")).toContainText("STATUS: ACTUAL");
  // ADR-0012: an actual is a fact. The record can be corrected; it cannot be
  // removed, and the interface points at the correction rather than hiding.
  await expect(page.getByTestId("event-screen-remove-refused")).toContainText("CORRECTIONS ONLY");
  await expect(page.getByTestId("event-screen-remove")).toHaveCount(0);
  await expect(page.getByTestId("event-screen-go-correct")).toBeVisible();
});

test("a forecast event can be removed, through a card like everything else", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `remove-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);

  const auth = { Authorization: `Bearer ${token}` };
  const created = await request.post("/api/book/edits", {
    headers: auth,
    data: {
      origin: "cell_edit",
      ops: [{ op: "add_event", date: "2026-10-12", amount: "-1800.00", note: "October trip" }],
    },
  });
  const proposal = ((await created.json()) as { proposal: { id: string } }).proposal;
  await request.post(`/api/proposals/${proposal.id}`, { headers: auth, data: { action: "accept" } });

  const ledger = await readJson<Ledger>(request, token, "/book/events?include_voided=true");
  const forecast = ledger.events.find((e) => e.note === "October trip");
  expect(forecast).toBeTruthy();

  await page.goto(`/event?id=${forecast!.id}`);
  await expect(page.getByTestId("event-screen-status-card")).toContainText("STATUS: FORECAST");
  await expect(page.getByTestId("event-screen-status-explainer")).toContainText("ledger takes over");

  await page.getByTestId("event-screen-remove").click();
  const card = page.getByTestId("event-screen-proposal-card");
  await expect(card).toBeVisible();
  await expect(card.getByTestId("event-screen-proposal-card-label")).toContainText(
    "PENDING · REMOVE_EVENT",
  );

  // Still there while the card is pending: the card is the change.
  const midway = await readJson<Ledger>(request, token, "/book/events?include_voided=true");
  expect(midway.events.some((e) => e.id === forecast!.id)).toBe(true);

  await card.getByTestId("event-screen-proposal-card-apply").click();
  await expect(page.getByTestId("event-screen-resolution")).toContainText("APPLIED");
});

test("a trace row leads to the item whose rule made the figure", async ({ page, request }) => {
  const token = await signIn(page, request, `taxonomy-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);

  await page.goto("/trace?period=2026-03-01&scenario=base");
  await expect(page.getByTestId("trace-screen-receipt")).toBeVisible();
  await page.getByTestId("trace-screen-row-rent").click();
  await expect(page.getByTestId("trace-screen-detail")).toBeVisible();

  // The taxonomy's other half: the edits for a generated row live where the
  // segment does (ADR-0013, SPEC §5-F3).
  await page.getByTestId("trace-screen-open-item").click();
  await expect(page.getByTestId("item-screen-title")).toHaveText("Rent");
});

test("every book setting is a proposal, and deletion needs the phrase", async ({
  page,
  request,
}) => {
  const token = await signIn(page, request, `settings-${Date.now()}@example.com`);
  await seedBook(request, token);
  await seedItems(request, token);

  await page.goto("/settings");
  await expect(page.getByTestId("settings-screen")).toBeVisible();
  await expect(page.getByTestId("settings-screen-email")).toContainText("@example.com");

  // R12, read-only. The seed committed once, so there is a revision to list.
  await expect(page.getByTestId("settings-screen-history-card")).toBeVisible();
  const state = await readJson<State>(request, token, "/book/state?scenario=base");
  expect(state.revision).not.toBeNull();
  await expect(
    page.getByTestId(`settings-screen-revision-${state.revision!.slice(0, 7)}`),
  ).toBeVisible();

  // About: the engine version and the current revision, as SPEC §6-S15 asks.
  await expect(page.getByTestId("settings-screen-engine-version")).toContainText("DETERMINISTIC");
  await expect(page.getByTestId("settings-screen-revision")).toContainText(
    state.revision!.slice(0, 7),
  );

  // The opening balance is a host op and therefore a card, not a save.
  await page.getByTestId("settings-screen-opening").fill("3000.00");
  await page.getByTestId("settings-screen-opening-submit").click();
  const card = page.getByTestId("settings-screen-proposal-card");
  await expect(card).toBeVisible();
  await expect(card.getByTestId("settings-screen-proposal-card-label")).toContainText(
    "PENDING · SET_OPENING_BALANCE",
  );

  const midway = await readJson<State>(request, token, "/book/state?scenario=base");
  expect(midway.book.opening_balance.exact).toBe(state.book.opening_balance.exact);

  await card.getByTestId("settings-screen-proposal-card-apply").click();
  await expect(page.getByTestId("settings-screen-resolution")).toContainText("APPLIED");
  const after = await readJson<State>(request, token, "/book/state?scenario=base");
  expect(after.book.opening_balance.exact).not.toBe(state.book.opening_balance.exact);

  // Deletion is behind the typed phrase, and nothing else unlocks it.
  await expect(page.getByTestId("settings-screen-delete-submit")).toBeDisabled();
  await page.getByTestId("settings-screen-delete-phrase").fill("yes");
  await expect(page.getByTestId("settings-screen-delete-submit")).toBeDisabled();
  await page.getByTestId("settings-screen-delete-phrase").fill("delete my account");
  await expect(page.getByTestId("settings-screen-delete-submit")).toBeEnabled();

  // There is no threshold surface anywhere on this screen (D-MLP-05b).
  const rendered = (await page.getByTestId("settings-screen").innerText()).toLowerCase();
  for (const word of ["threshold", "alert rule", "notify me when"]) {
    expect(rendered, `"${word}" is a post-MLP feature and is not scaffolded`).not.toContain(word);
  }
});

test("the privacy page carries the subprocessor list SPEC §9 requires", async ({
  page,
  request,
}) => {
  // SPEC §9: the list is *published on the privacy page* before any external
  // user. S4 deliberately left this section named and empty (D-MLP-71) rather
  // than inventing vendor names; this asserts S6 filled it, on the screen and
  // not only in a markdown file nobody reads.
  const token = await signIn(page, request, `privacy-${Date.now()}@example.com`);
  await seedBook(request, token);
  await page.goto("/settings");
  await expect(page.getByTestId("settings-screen-privacy-card")).toBeVisible();

  const list = page.getByTestId("settings-screen-subprocessors");
  await expect(list).toBeVisible();
  for (const vendor of ["hetzner", "openrouter", "google", "sentry", "grafana"]) {
    await expect(page.getByTestId(`settings-screen-subprocessor-${vendor}`)).toBeVisible();
  }

  // The absences are a disclosure too: a reader cannot infer "no speech
  // vendor" from a list that simply does not mention speech (D-MLP-45).
  const absent = (await page.getByTestId("settings-screen-subprocessors-absent").innerText())
    .toLowerCase();
  expect(absent).toContain("speech");
  expect(absent).toContain("bank aggregator");

  // The three retention periods, which the privacy policy also states and a
  // service test ties to the settings the service enforces.
  const retention = await page.getByTestId("settings-screen-retention").innerText();
  expect(retention).toContain("30 days");
  expect(retention).toContain("90");
});
