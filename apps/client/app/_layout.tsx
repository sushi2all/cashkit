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
  const segments: string[] = useSegments();
  const inAuth = segments[0] === "auth";

  React.useEffect(() => {
    if (status === "loading") return;
    if (status === "signed-out" && !inAuth) router.replace("/auth");
    if (status === "signed-in" && segments[0] === "auth" && segments[1] !== "verify") {
      router.replace("/");
    }
  }, [status, inAuth, segments, router]);

  if (status === "loading") return <LoadingState label="Signing you in…" />;
  return <>{children}</>;
}

function WithBook({ children }: { children: React.ReactNode }) {
  const book = useBook();
  const onBookChanged = useCallback(() => book.refresh(), [book]);
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
