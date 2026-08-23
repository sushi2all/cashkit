/**
 * The book's committed state, and the two acts that change what is committed.
 *
 * The header figures on Home come from here, and they are always **base**
 * figures (SPEC §2.4): this provider never reads a scenario override and never
 * merges a pending proposal into what it exposes. A turn that produces a
 * hypothetical answer changes what the answer card shows; it does not change
 * what this provider holds. That separation is ADR-0024's whole point, and
 * keeping it in the data layer means no screen can get it wrong by accident.
 *
 * **`?scenario=base` is not decoration.** Omitting it makes the service
 * resolve the read against `books.active_scenario` (SPEC §2.4, `reads.py`), so
 * the moment a fork is activated the Home header would start showing the
 * fork's figures — the exact thing §2.4 forbids:
 *
 * > The Home header and sparkline always show base committed figures, in
 * > neutral form, even while a fork is active; a fork's own figures render
 * > stamped with the fork's name.
 *
 * The parameter is what makes that true structurally rather than by care. The
 * active scenario is still exposed, as `activeScenario`, because a screen has
 * to be able to say which context the user is working in — it just must not
 * take its figures from there.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import type { BookState } from "@cashkit/api-types";

import { api, describeError } from "../api/client";

/** The plan of record. This provider reads nothing else (SPEC §2.4). */
export const BASE_SCENARIO = "base";

interface BookValue {
  /** Base committed state, always. Never a fork, never a pending change. */
  state: BookState | null;
  /**
   * Which scenario the user is working in, from the same payload's
   * `active_scenario` field. It names a context; it never supplies a figure.
   */
  activeScenario: string;
  loading: boolean;
  error: string | null;
  /** Re-read the committed state. Called after every accept, save and discard. */
  refresh: () => Promise<void>;
  save: (message: string) => Promise<{ ok: boolean; error?: string }>;
  discard: () => Promise<{ ok: boolean; error?: string }>;
}

const BookContext = createContext<BookValue | null>(null);

export function BookProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<BookState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    const { data, error: err, response } = await api.GET("/book/state", {
      params: { query: { scenario: BASE_SCENARIO } },
    });
    if (err || !data) {
      // 404 is not an error state: it means this account has no book yet, and
      // the onboarding path (S5) is what answers that.
      setState(null);
      setError(response.status === 404 ? null : describeError(err, response.status));
    } else {
      setState(data);
      setError(null);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const save = useCallback(
    async (message: string) => {
      const { error: err, response } = await api.POST("/book/save", { body: { message } });
      if (err) return { ok: false, error: describeError(err, response.status) };
      await refresh();
      return { ok: true };
    },
    [refresh],
  );

  const discard = useCallback(async () => {
    const { error: err, response } = await api.POST("/book/discard", {});
    if (err) return { ok: false, error: describeError(err, response.status) };
    await refresh();
    return { ok: true };
  }, [refresh]);

  const value = useMemo<BookValue>(
    () => ({
      state,
      activeScenario: state?.active_scenario ?? BASE_SCENARIO,
      loading,
      error,
      refresh,
      save,
      discard,
    }),
    [state, loading, error, refresh, save, discard],
  );

  return <BookContext.Provider value={value}>{children}</BookContext.Provider>;
}

export function useBook(): BookValue {
  const value = useContext(BookContext);
  if (!value) throw new Error("useBook must be used inside a BookProvider");
  return value;
}
