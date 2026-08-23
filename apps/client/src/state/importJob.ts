/**
 * One spreadsheet import: upload, live progress, report, and the one card.
 *
 * The service does the work in a background task and streams its progress
 * (SPEC §3, §7). This hook is the client half of that contract, and it holds
 * three rules:
 *
 *  1. **Nothing here applies anything.** The import produces one proposal
 *     (origin `import`) and the screen confirms it through `useEditProposal`,
 *     exactly as every other UI-origin write does (ADR-0029). There is no
 *     optimistic path.
 *  2. **Every figure comes from the stream.** The reconciliation report is
 *     rendered as it arrived; nothing is summed, compared or re-derived here.
 *  3. **The stream replays, so reconnecting is safe.** The service buffers
 *     everything it has emitted and replays it to a late listener (D-MLP-83),
 *     which is what makes the polling fallback below a fallback rather than a
 *     second implementation.
 *
 * **Why raw `fetch` and not the generated client.** `openapi-fetch` reads a
 * response body to completion; a progress stream has to be read as it arrives.
 * The types are still the generated ones — only the transport is hand-rolled,
 * and only for this one route.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { ImportDone, ImportStarted } from "@cashkit/api-types";

import { api, API_BASE_URL, describeError } from "../api/client";
import { loadSession } from "../api/tokenStore";

/** One progress frame. `stage` is the discriminator the service sets. */
export interface ImportEvent {
  stage: string;
  [key: string]: unknown;
}

export interface ImportJobState {
  started: ImportStarted | null;
  events: ImportEvent[];
  done: ImportDone | null;
  /** A transport or service failure — never a diagnostic (ADR-0015). */
  error: string | null;
  busy: boolean;
  /** Set when a SPEC §8 limit refused the import. A sentence, not an error. */
  refusal: ImportStarted | null;
  start: (file: unknown, name: string) => Promise<ImportDone | null>;
  reset: () => void;
}

const TERMINAL = new Set(["done", "failed"]);

export function useImportJob(): ImportJobState {
  const [started, setStarted] = useState<ImportStarted | null>(null);
  const [refusal, setRefusal] = useState<ImportStarted | null>(null);
  const [events, setEvents] = useState<ImportEvent[]>([]);
  const [done, setDone] = useState<ImportDone | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const cancelled = useRef(false);

  useEffect(() => () => {
    cancelled.current = true;
  }, []);

  const reset = useCallback(() => {
    setStarted(null);
    setRefusal(null);
    setEvents([]);
    setDone(null);
    setError(null);
  }, []);

  const start = useCallback<ImportJobState["start"]>(async (file, name) => {
    setBusy(true);
    setStarted(null);
    setRefusal(null);
    setEvents([]);
    setDone(null);
    setError(null);

    const { data, error: err, response } = await api.POST("/import", {
      body: { file: file as never },
      bodySerializer(body: { file: unknown }) {
        const form = new FormData();
        form.append("file", body.file as Blob, name);
        return form;
      },
    });
    if (err || !data) {
      setBusy(false);
      setError(describeError(err, response.status));
      return null;
    }
    if (data.kind === "refusal") {
      // SPEC §8, D-MLP-81: a limit is a sentence on a 200, not an error state.
      setBusy(false);
      setRefusal(data);
      return null;
    }
    setStarted(data);

    const terminal = await readStream(data.job_id, (event) => {
      if (!cancelled.current) setEvents((current) => [...current, event]);
    });
    setBusy(false);
    if (terminal === null) {
      setError("The import stopped and its progress could not be read.");
      return null;
    }
    if (terminal.stage === "failed") {
      setError(String(terminal.error ?? "The import did not finish."));
      return null;
    }
    const finished = terminal as unknown as ImportDone;
    setDone(finished);
    return finished;
  }, []);

  return { started, events, done, error, busy, refusal, start, reset };
}

/**
 * Read the SSE stream to its terminal frame, or fall back to a poll.
 *
 * React Native's `fetch` has no `ReadableStream` body, so `response.body` is
 * absent there; `GET /imports/{id}` returns the same terminal payload for that
 * case (D-MLP-77). Web gets the live frames, which is where import lives in the
 * MLP anyway (SPEC §6-S14).
 */
async function readStream(
  jobId: string,
  onEvent: (event: ImportEvent) => void,
): Promise<ImportEvent | null> {
  const session = await loadSession();
  const headers = session ? { Authorization: `Bearer ${session.token}` } : undefined;
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/imports/${jobId}/stream`, { headers });
  } catch {
    return pollUntilDone(jobId);
  }
  const body = (response as { body?: ReadableStream<Uint8Array> | null }).body;
  if (!response.ok || !body || typeof body.getReader !== "function") {
    return pollUntilDone(jobId);
  }

  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal: ImportEvent | null = null;

  for (;;) {
    const { done: finished, value } = await reader.read();
    if (value) buffer += decoder.decode(value, { stream: true });
    let split = buffer.indexOf("\n\n");
    while (split >= 0) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const event = parseFrame(frame);
      if (event) {
        onEvent(event);
        if (TERMINAL.has(event.stage)) terminal = event;
      }
      split = buffer.indexOf("\n\n");
    }
    if (terminal) {
      await reader.cancel().catch(() => undefined);
      return terminal;
    }
    if (finished) return terminal;
  }
}

/** `event: <stage>\ndata: <json>` — the comment frames are keep-alives. */
function parseFrame(frame: string): ImportEvent | null {
  const line = frame.split("\n").find((part) => part.startsWith("data: "));
  if (!line) return null;
  try {
    const parsed: unknown = JSON.parse(line.slice(6));
    if (parsed && typeof parsed === "object") return parsed as ImportEvent;
  } catch {
    return null;
  }
  return null;
}

const POLL_INTERVAL_MS = 1500;
const POLL_LIMIT = 80;

async function pollUntilDone(jobId: string): Promise<ImportEvent | null> {
  for (let attempt = 0; attempt < POLL_LIMIT; attempt += 1) {
    const { data, response } = await api.GET("/imports/{job_id}", {
      params: { path: { job_id: jobId } },
    });
    if (data) return { ...(data as unknown as ImportEvent), stage: "done" };
    if (response.status !== 409) return null;
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
  return null;
}
