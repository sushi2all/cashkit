/**
 * Screen 12 — Auth (SPEC §6-S12).
 *
 * Email magic link, single-use, 15-minute TTL (SPEC §3). Two properties worth
 * stating because they are easy to erode:
 *
 *  * **The token never comes back in a response.** It arrives by mail and
 *    returns through a deep link. There is no development shortcut in this
 *    screen that shows it, because the service has no mode that returns it.
 *  * **The service answers the same way whether or not the address has an
 *    account** — an endpoint that distinguished them would enumerate accounts.
 *    So "check your mail" is what this screen says either way, and it does not
 *    pretend to know more.
 */
import React, { useCallback, useState } from "react";
import { Text, TextInput, View, StyleSheet } from "react-native";

import { Button, Stamp } from "../ui/atoms";
import { ErrorState } from "../ui/states";
import { color, font, radius, space } from "../ui/tokens";

type Phase = "form" | "sent" | "verifying";

export function AuthScreen({
  onRequestLink,
  initialError,
  onDismissError,
  testID = "auth-screen",
}: {
  onRequestLink: (email: string) => Promise<void>;
  /** A link that was expired or already used lands here (SPEC §6-S12). */
  initialError?: string | null;
  onDismissError?: () => void;
  testID?: string;
}) {
  const [email, setEmail] = useState("");
  const [phase, setPhase] = useState<Phase>("form");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const send = useCallback(async () => {
    const address = email.trim();
    if (!address || busy) return;
    setBusy(true);
    setError(null);
    onDismissError?.();
    try {
      await onRequestLink(address);
      setPhase("sent");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }, [email, busy, onRequestLink, onDismissError]);

  return (
    <View testID={testID} style={styles.screen}>
      <View style={styles.header}>
        <Text style={styles.wordmark}>CashKit</Text>
        <Text style={styles.tagline}>
          Describe your money in plain words. Get an exact, explainable forecast.
        </Text>
      </View>

      {initialError ? (
        <View testID={`${testID}-link-error`} style={styles.linkError}>
          <ErrorState message={initialError} />
        </View>
      ) : null}

      {phase === "sent" ? (
        <View testID={`${testID}-sent`} style={styles.panel}>
          <Text style={styles.sentTitle}>Check your mail.</Text>
          <Text style={styles.sentBody}>
            If {email.trim()} has an account, a sign-in link is on its way. It works once and lasts
            fifteen minutes.
          </Text>
          <Button
            label="Use a different address"
            testID={`${testID}-restart`}
            onPress={() => {
              setPhase("form");
              setError(null);
            }}
          />
        </View>
      ) : (
        <View style={styles.panel}>
          <Text style={styles.label}>Email</Text>
          <TextInput
            testID={`${testID}-email`}
            accessibilityLabel="Email address"
            style={styles.input}
            value={email}
            onChangeText={setEmail}
            placeholder="you@example.com"
            placeholderTextColor={color.faint}
            autoCapitalize="none"
            autoCorrect={false}
            inputMode="email"
            onSubmitEditing={() => void send()}
            returnKeyType="go"
          />
          {error ? (
            <Text testID={`${testID}-error`} style={styles.error}>
              {error}
            </Text>
          ) : null}
          <View style={styles.actions}>
            <Button
              label={busy ? "Sending…" : "Send me a link"}
              variant="primary"
              disabled={busy || email.trim().length === 0}
              onPress={() => void send()}
              testID={`${testID}-submit`}
            />
          </View>
        </View>
      )}

      <View style={styles.footer}>
        <Stamp>NO PASSWORD · SINGLE-USE LINK · 15 MINUTES</Stamp>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: color.paper,
    paddingHorizontal: space.screenX,
    paddingTop: space.screenTop,
    paddingBottom: space.screenBottom,
    gap: 24,
  },
  header: { gap: 10 },
  wordmark: { fontFamily: font.display, fontSize: 40, fontWeight: "500", color: color.ink },
  tagline: { fontFamily: font.ui, fontSize: 15, color: color.sub, maxWidth: 340 },
  panel: { gap: 10 },
  label: { fontFamily: font.mono, fontSize: 9.5, letterSpacing: 0.9, color: color.sub },
  input: {
    height: 48,
    borderRadius: radius.card,
    borderWidth: 1,
    borderColor: color.hair,
    backgroundColor: color.card,
    paddingHorizontal: 16,
    fontFamily: font.ui,
    fontSize: 15,
    color: color.ink,
    outlineStyle: "none",
  } as object,
  actions: { flexDirection: "row", marginTop: 4 },
  error: { fontFamily: font.ui, fontSize: 13, color: color.rust },
  linkError: { width: "100%" },
  sentTitle: { fontFamily: font.display, fontSize: 22, fontWeight: "600", color: color.ink },
  sentBody: { fontFamily: font.ui, fontSize: 14, color: color.sub, marginBottom: 6 },
  footer: { marginTop: "auto" },
});
