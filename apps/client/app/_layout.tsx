/**
 * The app shell: providers, and the one gate between signed-out and signed-in.
 *
 * `expo-router` gives web URLs and native deep links the same route table, so
 * the magic link lands on `/auth/verify?token=…` whether it arrived as an
 * `https://` URL in a browser or as `cashkit://auth/verify` on a development
 * build (SPEC §3).
 */
import React, { useCallback } from "react";
import { View, StyleSheet } from "react-native";
import { Slot, useRouter, useSegments } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { BookProvider, useBook } from "../src/state/book";
import { ConversationProvider } from "../src/state/conversation";
import { SessionProvider, useSession } from "../src/state/session";
import { LoadingState } from "../src/ui/states";
import { color } from "../src/ui/tokens";

function Gate({ children }: { children: React.ReactNode }) {
  const { status } = useSession();
  const router = useRouter();
  // `useSegments()` is typed as a tuple of the known route shapes; the gate
  // only cares about the first two path parts, so read them as plain strings.
  //
  // The path is joined into a **string** before it reaches the dependency
  // array. `useSegments()` returns a fresh array on every render, so depending
  // on the array itself re-runs this effect every render — and since the
  // effect can navigate, that is an infinite redirect loop that crashes the
  // page rather than a wasted render.
  const segments: string[] = useSegments();
  const path = segments.join("/");

  React.useEffect(() => {
    if (status === "loading") return;
    const parts = path.split("/");
    const inAuthRoute = parts[0] === "auth";
    if (status === "signed-out" && !inAuthRoute) router.replace("/auth");
    if (status === "signed-in" && inAuthRoute && parts[1] !== "verify") router.replace("/");
  }, [status, path, router]);

  if (status === "loading") return <LoadingState label="Signing you in…" />;
  return <>{children}</>;
}

function WithBook({ children }: { children: React.ReactNode }) {
  // Depend on `refresh`, which is stable, rather than on the context value,
  // which is a new object whenever any part of the book state moves.
  const { refresh } = useBook();
  const onBookChanged = useCallback(() => refresh(), [refresh]);
  return <ConversationProvider onBookChanged={onBookChanged}>{children}</ConversationProvider>;
}

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <View style={styles.root}>
        <SessionProvider>
          <BookProvider>
            <WithBook>
              <Gate>
                <Slot />
              </Gate>
            </WithBook>
          </BookProvider>
        </SessionProvider>
      </View>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: color.paper, maxWidth: 720, width: "100%", alignSelf: "center" },
});
