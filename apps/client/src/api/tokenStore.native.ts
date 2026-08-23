/**
 * Session token storage — iOS and Android.
 *
 * SPEC §3: mobile stores the bearer in SecureStore, which is the Keychain on
 * iOS and the Keystore-backed shared preferences on Android. The web variant
 * lives in `tokenStore.ts`; Metro chooses between them per platform.
 */
import * as SecureStore from "expo-secure-store";

const KEY = "cashkit.session";

export interface StoredSession {
  token: string;
  expiresAt: string;
  platform: "web" | "mobile";
}

/** Which link shape this platform asks the service to send (SPEC §3). */
export const LINK_PLATFORM: "web" | "mobile" = "mobile";

export async function loadSession(): Promise<StoredSession | null> {
  const raw = await SecureStore.getItemAsync(KEY);
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && typeof (parsed as StoredSession).token === "string") {
      return parsed as StoredSession;
    }
    return null;
  } catch {
    return null;
  }
}

export async function saveSession(session: StoredSession): Promise<void> {
  await SecureStore.setItemAsync(KEY, JSON.stringify(session), {
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  });
}

export async function clearSession(): Promise<void> {
  await SecureStore.deleteItemAsync(KEY);
}
