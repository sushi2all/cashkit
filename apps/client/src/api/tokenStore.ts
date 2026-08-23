/**
 * Session token storage — web (and the default).
 *
 * SPEC §3 allows the web app to exchange the link for an httpOnly cookie or to
 * hold the bearer itself. The service issues a bearer and sets no cookie, and
 * changing that is a service change this session does not own, so the web app
 * holds the bearer in `localStorage` and S6 owns the cookie upgrade
 * (D-MLP-44). The trade is recorded rather than hidden: a bearer in
 * `localStorage` is reachable from any script that gets onto the page.
 *
 * Metro picks `tokenStore.native.ts` on iOS and Android, where the bearer goes
 * into the Keychain/Keystore through SecureStore instead.
 */
const KEY = "cashkit.session";

export interface StoredSession {
  token: string;
  expiresAt: string;
  platform: "web" | "mobile";
}

/** Which link shape this platform asks the service to send (SPEC §3). */
export const LINK_PLATFORM: "web" | "mobile" = "web";

function storage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

export async function loadSession(): Promise<StoredSession | null> {
  const store = storage();
  if (!store) return null;
  const raw = store.getItem(KEY);
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      parsed &&
      typeof parsed === "object" &&
      typeof (parsed as StoredSession).token === "string"
    ) {
      return parsed as StoredSession;
    }
    return null;
  } catch {
    return null;
  }
}

export async function saveSession(session: StoredSession): Promise<void> {
  storage()?.setItem(KEY, JSON.stringify(session));
}

export async function clearSession(): Promise<void> {
  storage()?.removeItem(KEY);
}
