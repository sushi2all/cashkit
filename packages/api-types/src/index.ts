/**
 * The service contract, as the client sees it.
 *
 * Everything in `./generated/schema` is build output from
 * `apps/service/openapi.json`. This file adds only two things, neither of
 * which describes an endpoint: readable aliases for the schemas the UI names
 * often, and a typed `fetch` wrapper. No request shape, no response shape and
 * no path is written by hand — `npm run api:check-drift` fails the build the
 * moment the committed types stop matching the service.
 */
import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./generated/schema";

export type { components, paths } from "./generated/schema";

type Schemas = components["schemas"];

/**
 * One money figure, in the only two forms the API ever ships (D-MLP-06).
 *
 * `exact` is the engine's lossless 4dp value; `display` is the same value at
 * 2dp, rounded the way the engine rounds. **The client renders these strings.
 * It never computes one.** See `apps/client/src/money` for the rendering
 * primitives and the lint rule that enforces it.
 */
export type Money = Schemas["Money"];

export type WhatIf = Schemas["WhatIf"];
export type Diagnostic = Schemas["DiagnosticOut"];
export type TurnRequest = Schemas["TurnRequest"];
export type TurnResponse = Schemas["TurnResponse"];
export type TurnKind = TurnResponse["kind"];
export type Receipt = Schemas["Receipt"];
export type Proposal = Schemas["ProposalOut"];
export type Deltas = Schemas["Deltas"];
export type Crossing = Schemas["Crossing"];
export type MoneyMove = Schemas["MoneyMove"];
export type PeriodMove = Schemas["PeriodMove"];
export type AcceptResponse = Schemas["AcceptResponse"];
export type ProposalResponse = Schemas["ProposalResponse"];
export type BookState = Schemas["BookState"];
export type BookParams = Schemas["BookParams"];
export type Summary = Schemas["SummaryOut"];
export type Warnings = Schemas["Warnings"];
export type NegativeMonth = Schemas["NegativeMonth"];
export type ItemSeries = Schemas["ItemSeries"];
export type Forecast = Schemas["Forecast"];
export type ForecastRow = Schemas["ForecastRow"];
export type TraceResponse = Schemas["TraceResponse"];
export type Trace = Schemas["TraceOut"];
export type TraceStep = Schemas["StepOut"];
export type TraceBinding = Schemas["BindingOut"];
export type WhyZeroResponse = Schemas["WhyZeroResponse"];
export type Explanation = Schemas["ExplanationOut"];
export type Session = Schemas["Session"];
export type Me = Schemas["Me"];
export type BookCreated = Schemas["BookCreated"];
export type SaveResponse = Schemas["SaveResponse"];
export type DiscardResponse = Schemas["DiscardResponse"];
export type ScenariosResponse = Schemas["ScenariosResponse"];
export type Scenario = Schemas["ScenarioOut"];

/* --- F4: scenarios and compare (SPEC §5-F4, R9) ------------------------- */
export type CompareResponse = Schemas["CompareResponse"];
export type ComparePeriod = Schemas["ComparePeriod"];
export type ActivateResponse = Schemas["ActivateResponse"];

/* --- F5: the ledger, reconciliation and validate (SPEC §5-F5, R10) ------ */
export type EventsResponse = Schemas["EventsResponse"];
export type LedgerEvent = Schemas["EventOut"];
export type ReconcileResponse = Schemas["ReconcileResponse"];
export type Reconciliation = Schemas["ReconciliationOut"];
export type ReconciliationLine = Schemas["ReconciliationLineOut"];
export type ValidateResponse = Schemas["ValidateResponse"];

/* --- S14: spreadsheet import and export (SPEC §7, §6-S14) --------------- */
export type ImportStarted = Schemas["ImportStarted"];
export type ImportTarget = Schemas["ImportTarget"];
export type ImportDone = Schemas["ImportDone"];
export type ReconciliationReport = Schemas["ReconciliationReport"];
export type ImportCheck = Schemas["CheckResult"];
export type ImportCheckStatus = ImportCheck["status"];

/* --- S15: settings, history and the account (R12) ----------------------- */
export type HistoryResponse = Schemas["HistoryResponse"];
export type Revision = Schemas["RevisionOut"];

/**
 * One operation in a `POST /book/edits` proposal.
 *
 * The union is the service's own: the 21-intent mutation set plus the five
 * host ops of SPEC §2.5. The UI composes host ops; a model never sees them.
 */
export type EditOperation = Schemas["EditsRequest"]["ops"][number];
export type EditOrigin = Schemas["EditsRequest"]["origin"];

export type CashKitClient = ReturnType<typeof createCashKitClient>;

export interface ClientOptions {
  baseUrl: string;
  /** Returns the bearer for the current session, or null when signed out. */
  getToken?: () => string | null | Promise<string | null>;
  /** Called when the service rejects the session, so the app can sign out. */
  onUnauthorized?: () => void;
  fetch?: typeof globalThis.fetch;
}

/**
 * A typed client over the generated paths.
 *
 * The bearer is attached per request rather than baked in at construction, so
 * signing in and out never rebuilds the client. `x-request-id` is not set
 * here: the service mints one and echoes it in every envelope (SPEC §11), and
 * a client-minted id would break that chain's single source.
 */
export function createCashKitClient(options: ClientOptions) {
  const client = createClient<paths>({
    baseUrl: options.baseUrl,
    fetch: options.fetch,
  });

  const auth: Middleware = {
    async onRequest({ request }) {
      const token = await options.getToken?.();
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
      return request;
    },
    async onResponse({ response }) {
      if (response.status === 401) options.onUnauthorized?.();
      return response;
    },
  };
  client.use(auth);
  return client;
}
