/**
 * The mic-only input of SPEC §6: "Forecast and Scenarios keep the mic only."
 *
 * Same adapter, same compliance rule and same fail-closed behaviour as the
 * full ask bar (D-MLP-45): where the platform cannot recognize speech
 * on-device, the control is not shown and one sentence says why, rather than a
 * dead button. What the service receives is text either way.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Pressable, Text, View, StyleSheet } from "react-native";

import { capability, start, type DictationCapability, type DictationSession } from "../../voice/dictation";
import { color, font, radius } from "../../ui/tokens";

export function MicButton({
  onTranscript,
  disabled = false,
  testID = "mic",
}: {
  /** Called with the final transcript when the user stops dictating. */
  onTranscript: (text: string) => void;
  disabled?: boolean;
  testID?: string;
}) {
  const [listening, setListening] = useState(false);
  const [voice, setVoice] = useState<DictationCapability | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const session = useRef<DictationSession | null>(null);
  const heard = useRef<string>("");

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
      const text = heard.current.trim();
      heard.current = "";
      if (text) onTranscript(text);
      return;
    }
    setVoiceError(null);
    heard.current = "";
    const started = await start({
      onResult: (transcript) => {
        heard.current = transcript;
      },
      onError: (message) => {
        setVoiceError(message);
        setListening(false);
      },
      onEnd: () => {
        setListening(false);
        const text = heard.current.trim();
        heard.current = "";
        if (text) onTranscript(text);
      },
    });
    session.current = started;
    setListening(started !== null);
  }, [listening, onTranscript]);

  if (voice && !voice.supported) {
    return voice.reason ? (
      <Text testID={`${testID}-unavailable`} style={styles.note}>
        {voice.reason}
      </Text>
    ) : null;
  }

  return (
    <View style={styles.row}>
      {voiceError ? (
        <Text testID={`${testID}-error`} style={styles.note}>
          {voiceError}
        </Text>
      ) : null}
      <Pressable
        testID={testID}
        accessibilityRole="button"
        accessibilityLabel={listening ? "Stop dictating" : "Dictate"}
        accessibilityState={{ selected: listening, disabled }}
        disabled={disabled}
        onPress={() => void toggle()}
        style={[styles.mic, listening && styles.micActive, disabled && styles.micDisabled]}
      >
        <Text style={styles.glyph}>{listening ? "■" : "●"}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
  note: { fontFamily: font.ui, fontSize: 11, color: color.sub, flexShrink: 1 },
  mic: {
    width: 40,
    height: 40,
    borderRadius: radius.pill,
    backgroundColor: color.pine,
    alignItems: "center",
    justifyContent: "center",
  },
  micActive: { backgroundColor: color.rust },
  micDisabled: { opacity: 0.4 },
  glyph: { color: "#FFFFFF", fontSize: 12 },
});
