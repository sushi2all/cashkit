/**
 * The book's committed state, and the two acts that change what is committed.
 *
 * The header figures on Home come from here, and they are always **base
 * committed** figures (SPEC §2.4): this provider never reads a scenario
 * override and never merges a pending proposal into what it exposes. A turn
 * that produces a hypothetical answer changes what the answer card shows; it
 * does not change what this provider holds. That separation is ADR-0024's
 * whole point, and keeping it in the data layer means no screen can get it
 * wrong by accident.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import type { BookState } from "@cashkit/api-types";

import { api, describeError } from "../api/client";

interface BookValue {
  state: BookState | null;
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
    const { data, error: err, response } = await api.GET("/book/state", {});
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
    () => ({ state, loading, error, refresh, save, discard }),
    [state, loading, error, refresh, save, discard],
  );

  return <BookContext.Provider value={value}>{children}</BookContext.Provider>;
}

export function useBook(): BookValue {
  const value = useContext(BookContext);
  if (!value) throw new Error("useBook must be used inside a BookProvider");
  return value;
}
