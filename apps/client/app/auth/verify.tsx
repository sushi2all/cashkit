/**
 * The deep-link landing route (SPEC §3, §6-S12).
 *
 * The magic-link token arrives here as a query parameter — from an `https://`
 * URL on web, or from the `cashkit://` scheme on a development build. It is
 * exchanged once for a bearer session; a link that has expired or has already
 * been used sends the user back to the sign-in screen with the reason, which
 * is the expired-link error state SPEC §6-S12 asks for.
 */
import React, { useEffect, useRef, useState } from "react";
import { View, StyleSheet } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { useSession } from "../../src/state/session";
import { ErrorState, LoadingState } from "../../src/ui/states";
import { color, space } from "../../src/ui/tokens";

export default function VerifyRoute() {
  const { token } = useLocalSearchParams<{ token?: string }>();
  const { verify } = useSession();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const attempted = useRef(false);

  useEffect(() => {
    if (attempted.current) return;
    if (typeof token !== "string" || token.length === 0) {
      setError("That link is missing its token. Ask for a new one.");
      return;
    }
    // A link token is single-use: exchanging it twice burns a valid session.
    attempted.current = true;
    void (async () => {
      try {
        await verify(token);
        router.replace("/");
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "That link did not work.");
      }
    })();
  }, [token, verify, router]);

  if (error) {
    return (
      <View testID="verify-error" style={styles.screen}>
        <ErrorState message={error} onRetry={() => router.replace("/auth")} testID="verify-error-state" />
      </View>
    );
  }
  return <LoadingState label="Signing you in…" testID="verify-loading" />;
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: color.paper,
    paddingHorizontal: space.screenX,
    justifyContent: "center",
  },
});
