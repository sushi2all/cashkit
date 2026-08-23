/**
 * The one API client instance the app uses.
 *
 * Every request goes through the generated types in `@cashkit/api-types`; no
 * path or payload shape is written here. The bearer is read from the platform
 * token store per request, so signing in and out never rebuilds the client.
 */
import { createCashKitClient, type CashKitClient } from "@cashkit/api-types";

import { loadSession } from "./tokenStore";

/**
 * Where the service is. `EXPO_PUBLIC_API_URL` is inlined into the bundle at
 * build time by Expo, which is correct for a public base URL and is the only
 * kind of value this app ever holds — there is no secret in a client bundle
 * (PROMPT non-negotiable 9).
 */
export const API_BASE_URL: string =
  process.env.EXPO_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

let signOutHandler: (() => void) | null = null;

/** Registered by the session provider so a 401 anywhere ends the session. */
export function onUnauthorized(handler: (() => void) | null): void {
  signOutHandler = handler;
}

export const api: CashKitClient = createCashKitClient({
  baseUrl: API_BASE_URL,
  getToken: async () => (await loadSession())?.token ?? null,
  onUnauthorized: () => signOutHandler?.(),
});

/**
 * The message to show when a request fails.
 *
 * Diagnostics are never routed through here: they are engine or host findings
 * with a code, a severity and a suggested fix, and they render verbatim in
 * their own element (ADR-0015). This is only for transport and status
 * failures, which are the app's problem, not the book's.
 */
export function describeError(error: unknown, status?: number): string {
  if (status === 503) return "The assistant is not reachable right now. Your book is untouched.";
  if (status === 502) return "The assistant did not answer. Nothing was changed.";
  if (status === 404) return "That is not there.";
  if (typeof error === "object" && error !== null && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail;
    // The service ships `{code, message}` (errors.py). The message is written
    // for a person, so it is what the person reads.
    if (typeof detail === "object" && detail !== null && "message" in detail) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
    if (typeof detail === "string") return detail;
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
