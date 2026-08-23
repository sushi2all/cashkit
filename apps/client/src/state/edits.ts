/**
 * The UI-origin write path: `POST /book/edits` → a card → `POST /proposals/{id}`.
 *
 * Every write that does not come from a turn comes from here — a settings
 * change, a cutover, a correction, a cell edit, "+ Add a date", "New scenario".
 * It is the same pipeline the turn path uses (SPEC §2.5): the service dry-runs
 * the operations and hands back a **proposal**, and nothing reaches the book
 * until the user confirms it (ADR-0029).
 *
 * Three rules this hook exists to keep, on every screen at once:
 *
 *  1. **No optimistic apply.** `propose()` stores what came back and renders
 *     it. `resolve()` posts the action and renders what came back. Neither
 *     touches the book's state locally, and neither guesses the outcome.
 *  2. **A refreshed card is re-presented, never retried.** When the ground
 *     moved under a pending card the service re-runs the dry-run and returns a
 *     different card with different numbers. It needs confirming again.
 *  3. **A clarification stores nothing.** The record-actual channel returns
 *     `kind: "clarification"` when the date is missing or ambiguous
 *     (SPEC §5-F5). That is rendered, not resolved into a guess.
 */
import { useCallback, useState } from "react";

import type {
  AcceptResponse,
  EditOperation,
  EditOrigin,
  Proposal,
  ProposalResponse,
} from "@cashkit/api-types";

import { api, describeError } from "../api/client";

export interface EditProposalState {
  /** The card awaiting the user, if any. */
  pending: Proposal | null;
  /** The service's answer to the last confirmation. */
  resolution: AcceptResponse | null;
  /** The service's question, when it could not build a card without one. */
  clarification: string | null;
  error: string | null;
  busy: boolean;
  propose: (
    ops: EditOperation[],
    options?: { origin?: EditOrigin; scenario?: string; context?: "actuals_record" },
  ) => Promise<ProposalResponse | null>;
  resolve: (action: "accept" | "discard") => Promise<AcceptResponse | null>;
  /**
   * Take over a card the service produced somewhere other than `propose()`.
   *
   * The import loop raises its proposal from `POST /import` (SPEC §7.4), so
   * there is no `POST /book/edits` call to return it — but it is the same row
   * in the same store, and it must be confirmed the same way. `adopt` stores
   * it and nothing else: `resolve()` still posts to the service and still
   * renders what came back, so no optimistic path is opened by it.
   */
  adopt: (proposal: Proposal) => void;
  reset: () => void;
}

export function useEditProposal(options?: {
  /** Called after an accept that applied, so the screen can re-read. */
  onApplied?: () => void | Promise<void>;
}): EditProposalState {
  const onApplied = options?.onApplied;
  const [pending, setPending] = useState<Proposal | null>(null);
  const [resolution, setResolution] = useState<AcceptResponse | null>(null);
  const [clarification, setClarification] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reset = useCallback(() => {
    setPending(null);
    setResolution(null);
    setClarification(null);
    setError(null);
  }, []);

  const adopt = useCallback<EditProposalState["adopt"]>((proposal) => {
    setResolution(null);
    setClarification(null);
    setError(null);
    setPending(proposal);
  }, []);

  const propose = useCallback<EditProposalState["propose"]>(async (ops, opts) => {
    setBusy(true);
    setError(null);
    setClarification(null);
    setResolution(null);
    const { data, error: err, response } = await api.POST("/book/edits", {
      body: {
        ops,
        origin: opts?.origin ?? "cell_edit",
        ...(opts?.scenario ? { scenario: opts.scenario } : {}),
        ...(opts?.context ? { context: opts.context } : {}),
      },
    });
    setBusy(false);
    if (err || !data) {
      setPending(null);
      setError(describeError(err, response.status));
      return null;
    }
    if (data.kind === "clarification") {
      // SPEC §5-F5: a missing or ambiguous date stores nothing and asks.
      setPending(null);
      setClarification(data.clarification ?? "I need one more detail before I can do that.");
      return data;
    }
    setPending(data.proposal ?? null);
    return data;
  }, []);

  const resolve = useCallback<EditProposalState["resolve"]>(
    async (action) => {
      if (!pending) return null;
      setBusy(true);
      setError(null);
      const { data, error: err, response } = await api.POST("/proposals/{proposal_id}", {
        params: { path: { proposal_id: pending.id } },
        body: { action },
      });
      setBusy(false);
      if (err || !data) {
        setError(describeError(err, response.status));
        return null;
      }
      setResolution(data);
      // A refreshed card is a different card. Present it; never retry it.
      setPending(data.kind === "refreshed" ? data.proposal : null);
      if (data.kind === "applied") await onApplied?.();
      return data;
    },
    [pending, onApplied],
  );

  return { pending, resolution, clarification, error, busy, propose, resolve, adopt, reset };
}
