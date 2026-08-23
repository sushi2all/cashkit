/**
 * Who is signed in, and how they got there.
 *
 * SPEC §3: email magic link, single-use token, bearer session. The link token
 * never appears in an API response — it arrives by mail and comes back to the
 * app through a deep link (`cashkit://auth/verify?token=`) or, on web, an
 * ordinary HTTPS URL. This provider owns the exchange and the platform token
 * store; nothing else in the app touches a token.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import type { Me } from "@cashkit/api-types";

import { api, describeError, onUnauthorized } from "../api/client";
import { LINK_PLATFORM, clearSession, loadSession, saveSession } from "../api/tokenStore";

type Status = "loading" | "signed-out" | "signed-in";

interface SessionValue {
  status: Status;
  me: Me | null;
  /** Ask the service to mail a link. Never returns the token — nothing does. */
  requestLink: (email: string) => Promise<void>;
  /** Exchange a link token for a session. */
  verify: (token: string) => Promise<void>;
  signOut: () => Promise<void>;
  refreshMe: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);

  const signOut = useCallback(async () => {
    await clearSession();
    setMe(null);
    setStatus("signed-out");
  }, []);

  const refreshMe = useCallback(async () => {
    const { data, error, response } = await api.GET("/me", {});
    if (error || !data) {
      if (response.status === 401) await signOut();
      return;
    }
    setMe(data);
    setStatus("signed-in");
  }, [signOut]);

  useEffect(() => {
    onUnauthorized(() => {
      void signOut();
    });
    return () => onUnauthorized(null);
  }, [signOut]);

  useEffect(() => {
    void (async () => {
      const stored = await loadSession();
      if (!stored) {
        setStatus("signed-out");
        return;
      }
      await refreshMe();
    })();
  }, [refreshMe]);

  const requestLink = useCallback(async (email: string) => {
    const { error, response } = await api.POST("/auth/link", {
      body: { email, platform: LINK_PLATFORM },
    });
    // The service answers the same way whether or not the address has an
    // account — an endpoint that distinguishes them enumerates accounts — so
    // there is nothing to report on success beyond "we sent it".
    if (error && response.status >= 400) {
      throw new Error(describeError(error, response.status));
    }
  }, []);

  const verify = useCallback(
    async (token: string) => {
      const { data, error, response } = await api.POST("/auth/verify", {
        body: { token, platform: LINK_PLATFORM },
      });
      if (error || !data) {
        throw new Error(
          response.status === 401 || response.status === 400
            ? "That link has expired or was already used. Ask for a new one."
            : describeError(error, response.status),
        );
      }
      await saveSession({
        token: data.token,
        expiresAt: data.expires_at,
        platform: data.platform,
      });
      await refreshMe();
    },
    [refreshMe],
  );

  const value = useMemo<SessionValue>(
    () => ({ status, me, requestLink, verify, signOut, refreshMe }),
    [status, me, requestLink, verify, signOut, refreshMe],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside a SessionProvider");
  return value;
}
