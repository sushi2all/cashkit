/**
 * The ask row: text plus a microphone (SPEC §6 shared inventory).
 *
 * Dictation is a platform adapter (SPEC §2.1) and a compliance boundary
 * (SPEC §9). The adapter decides whether this platform may dictate at all;
 * this component only reflects that decision, and when dictation is
 * unavailable it says why in one sentence instead of showing a dead button.
 * What the service receives is text either way.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Pressable, Text, TextInput, View, StyleSheet } from "react-native";

import { capability, start, type DictationCapability, type DictationSession } from "../../voice/dictation";
import { color, font, radius } from "../../ui/tokens";

export function AskBar({
  placeholder = "Ask or tell me anything…",
  onSubmit,
  disabled = false,
  value,
  onChangeValue,
  testID = "ask-bar",
}: {
  placeholder?: string;
  onSubmit: (text: string) => void;
  disabled?: boolean;
  value?: string;
  onChangeValue?: (text: string) => void;
  testID?: string;
}) {
  const [internal, setInternal] = useState("");
  const text = value ?? internal;
  const setText = useCallback(
    (next: string) => {
      if (onChangeValue) onChangeValue(next);
      else setInternal(next);
    },
    [onChangeValue],
  );

  const [listening, setListening] = useState(false);
  const [voice, setVoice] = useState<DictationCapability | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const session = useRef<DictationSession | null>(null);

  useEffect(() => {
    let cancelled = false;
    void capability().then((can) => {
      if (!cancelled) setVoice(can);
    });
    return () => {
      cancelled = true;
      session.current?.stop();
    };
  }, []);

  const toggle = useCallback(async () => {
    if (listening) {
      session.current?.stop();
      session.current = null;
      setListening(false);
      return;
    }
    setVoiceError(null);
    const started = await start({
      onResult: (transcript) => setText(transcript),
      onError: (message) => {
        setVoiceError(message);
        setListening(false);
      },
      onEnd: () => setListening(false),
    });
    session.current = started;
    setListening(started !== null);
  }, [listening, setText]);

  const submit = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    session.current?.stop();
    session.current = null;
    setListening(false);
    setText("");
    onSubmit(trimmed);
  }, [text, disabled, onSubmit, setText]);

  return (
    <View style={styles.wrap}>
      {voiceError ? (
        <Text testID={`${testID}-voice-error`} style={styles.voiceNote}>
          {voiceError}
        </Text>
      ) : voice && !voice.supported && voice.reason ? (
        <Text testID={`${testID}-voice-unavailable`} style={styles.voiceNote}>
          {voice.reason}
        </Text>
      ) : null}

      <View style={styles.row}>
        <View style={styles.field}>
          <TextInput
            testID={`${testID}-input`}
            accessibilityLabel="Ask or tell CashKit"
            style={styles.input}
            placeholder={listening ? "Listening…" : placeholder}
            placeholderTextColor={color.faint}
            value={text}
            editable={!disabled}
            onChangeText={setText}
            onSubmitEditing={submit}
            returnKeyType="send"
            multiline={false}
          />
        </View>
        {voice?.supported ? (
          <Pressable
            testID={`${testID}-mic`}
            accessibilityRole="button"
            accessibilityLabel={listening ? "Stop dictating" : "Dictate"}
            accessibilityState={{ selected: listening }}
            onPress={() => void toggle()}
            style={[styles.mic, listening && styles.micActive]}
          >
            <Text style={styles.micGlyph}>{listening ? "■" : "●"}</Text>
          </Pressable>
        ) : null}
        <Pressable
          testID={`${testID}-send`}
          accessibilityRole="button"
          accessibilityLabel="Send"
          onPress={submit}
          disabled={disabled || text.trim().length === 0}
          style={({ pressed }) => [
            styles.send,
            (disabled || text.trim().length === 0) && styles.sendDisabled,
            pressed && styles.pressed,
          ]}
        >
          <Text style={styles.sendGlyph}>↑</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { width: "100%", gap: 6 },
  row: { flexDirection: "row", gap: 10, width: "100%", alignItems: "center" },
  field: {
    flex: 1,
    height: 48,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: color.hair,
    backgroundColor: color.card,
    paddingHorizontal: 18,
    justifyContent: "center",
  },
  input: { fontFamily: font.ui, fontSize: 15, color: color.ink, outlineStyle: "none" } as object,
  mic: {
    width: 48,
    height: 48,
    borderRadius: radius.pill,
    backgroundColor: color.pine,
    alignItems: "center",
    justifyContent: "center",
  },
  micActive: { backgroundColor: color.rust },
  micGlyph: { color: "#FFFFFF", fontSize: 14 },
  send: {
    width: 48,
    height: 48,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: color.hair,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: color.card,
  },
  sendDisabled: { opacity: 0.4 },
  pressed: { opacity: 0.7 },
  sendGlyph: { color: color.ink, fontSize: 18 },
  voiceNote: { fontFamily: font.ui, fontSize: 11, color: color.sub },
});
