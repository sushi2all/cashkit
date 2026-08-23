/**
 * The turn loop: what the user said, what came back, and what is pending.
 *
 * Three rules from the SPEC shape this file, and each is easy to get wrong:
 *
 *  1. **`POST /turns` is not a write route** (S2 handoff §7). It creates a
 *     proposal and nothing else. The client never applies one itself — it
 *     posts `POST /proposals/{id} {action}` and renders whatever came back.
 *     There is no optimistic path here, not even a hidden one.
 *  2. **Accept can return a refreshed card instead of applied state** (SPEC
 *     §2.5). When the ground moved under a pending proposal, the service
 *     re-runs the dry-run and hands back a *new* card that still needs
 *     confirming. The UI re-presents it. It never retries silently, because a
 *     silent retry would apply a change the user confirmed against different
 *     numbers.
 *  3. **`kind` has four values** (D-MLP-24). `refusal` is a guardrail, arrives
 *     on a 200 with `retry_after_seconds`, and reads as a sentence. Rendering
 *     it as an error is the wrong shape.
 */
import React, { createContext, useCallback, useContext, useMemo, useState } from "react";

import type { AcceptResponse, TurnResponse } from "@cashkit/api-types";

import { api, describeError } from "../api/client";

export interface QuoteEntry {
  kind: "quote";
  id: string;
  text: string;
}

export interface TurnEntry {
  kind: "turn";
  id: string;
  response: TurnResponse;
}

/** The outcome of a confirmation: applied, discarded, or a refreshed card. */
export interface ResolutionEntry {
  kind: "resolution";
  id: string;
  response: AcceptResponse;
}

export interface FailureEntry {
  kind: "failure";
  id: string;
  message: string;
}

export type Entry = QuoteEntry | TurnEntry | ResolutionEntry | FailureEntry;

interface ConversationValue {
  entries: Entry[];
  busy: boolean;
  /** The card awaiting the user, if any. Only one is ever live at a time. */
  pendingProposalId: string | null;
  ask: (text: string, options?: { context?: "actuals_record"; scenario?: string }) => Promise<void>;
  resolve: (proposalId: string, action: "accept" | "discard") => Promise<void>;
  clear: () => void;
}

const ConversationContext = createContext<ConversationValue | null>(null);

let counter = 0;
const nextId = (): string => {
  counter += 1;
  return `e${counter}`;
};

export function ConversationProvider({
  children,
  onBookChanged,
}: {
  children: React.ReactNode;
  /** Called whenever the committed state may have moved, so Home can re-read it. */
  onBookChanged?: () => void | Promise<void>;
}) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [busy, setBusy] = useState(false);
  const [pendingProposalId, setPendingProposalId] = useState<string | null>(null);

  const push = useCallback((entry: Entry) => {
    setEntries((current) => [...current, entry]);
  }, []);

  const ask = useCallback(
    async (text: string, options?: { context?: "actuals_record"; scenario?: string }) => {
      const trimmed = text.trim();
      if (!trimmed || busy) return;
      setBusy(true);
      push({ kind: "quote", id: nextId(), text: trimmed });

      const body: { text: string; context?: "actuals_record"; scenario?: string } = { text: trimmed };
      if (options?.context) body.context = options.context;
      if (options?.scenario) body.scenario = options.scenario;

      const { data, error, response } = await api.POST("/turns", { body });
      if (error || !data) {
        push({ kind: "failure", id: nextId(), message: describeError(error, response.status) });
        setBusy(false);
        return;
      }
      push({ kind: "turn", id: nextId(), response: data });
      // A proposal turn leaves exactly one card live. Anything else clears it:
      // the service supersedes pending cards on its own side, and a card the
      // service has superseded must not stay tappable here.
      setPendingProposalId(data.kind === "proposal" && data.proposal ? data.proposal.id : null);
      setBusy(false);
    },
    [busy, push],
  );

  const resolve = useCallback(
    async (proposalId: string, action: "accept" | "discard") => {
      if (busy) return;
      setBusy(true);
      const { data, error, response } = await api.POST("/proposals/{proposal_id}", {
        params: { path: { proposal_id: proposalId } },
        body: { action },
      });
      if (error || !data) {
        push({ kind: "failure", id: nextId(), message: describeError(error, response.status) });
        setBusy(false);
        return;
      }
      push({ kind: "resolution", id: nextId(), response: data });

      if (data.kind === "refreshed") {
        // SPEC §2.5: the dry-run was re-run against the state that actually
        // exists now. This is a different card, with different numbers, and it
        // needs confirming again. Re-present, never retry.
        setPendingProposalId(data.proposal.id);
      } else {
        setPendingProposalId(null);
      }

      if (data.kind === "applied") await onBookChanged?.();
      setBusy(false);
    },
    [busy, push, onBookChanged],
  );

  const clear = useCallback(() => {
    setEntries([]);
    setPendingProposalId(null);
  }, []);

  const value = useMemo<ConversationValue>(
    () => ({ entries, busy, pendingProposalId, ask, resolve, clear }),
    [entries, busy, pendingProposalId, ask, resolve, clear],
  );

  return <ConversationContext.Provider value={value}>{children}</ConversationContext.Provider>;
}

export function useConversation(): ConversationValue {
  const value = useContext(ConversationContext);
  if (!value) throw new Error("useConversation must be used inside a ConversationProvider");
  return value;
}
