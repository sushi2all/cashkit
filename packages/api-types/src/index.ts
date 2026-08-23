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
