/**
 * Dictation — iOS and Android.
 *
 * SPEC §9 prefers on-device recognition, and both platforms offer it, so this
 * adapter **requires** it: `requiresOnDeviceRecognition: true`. There is no
 * cloud fallback. If a device cannot recognize locally, dictation reports
 * itself unavailable and the user types — which is the §9 rule applied rather
 * than worked around, and it keeps the mobile app off the subprocessor list
 * for speech entirely (D-MLP-45).
 */
import { ExpoSpeechRecognitionModule } from "expo-speech-recognition";

import type {
  DictationCapability,
  DictationHandlers,
  DictationSession,
} from "./dictation";

export type { DictationCapability, DictationHandlers, DictationSession, DictationState } from "./dictation";

const LANG = "en-GB";

export async function capability(): Promise<DictationCapability> {
  try {
    const available = ExpoSpeechRecognitionModule.isRecognitionAvailable();
    if (!available) {
      return { supported: false, onDevice: false, reason: "This device cannot dictate." };
    }
    // Two questions, and both have to answer yes before dictation is offered:
    // does this device recognize locally at all, and is the language actually
    // installed for offline use? A device that would silently fall back to a
    // cloud recognizer must not be offered dictation (SPEC §9).
    if (!ExpoSpeechRecognitionModule.supportsOnDeviceRecognition()) {
      return {
        supported: false,
        onDevice: false,
        reason: "This device cannot dictate without sending audio away, so it is off. Type instead.",
      };
    }
    const locales = await ExpoSpeechRecognitionModule.getSupportedLocales({});
    const installed = [...locales.installedLocales, ...locales.locales];
    const onDevice = installed.some((l) => l.toLowerCase().startsWith("en"));
    if (!onDevice) {
      return {
        supported: false,
        onDevice: false,
        reason: "On-device dictation is not installed for this language, so it is off. Type instead.",
      };
    }
    return { supported: true, onDevice: true };
  } catch {
    return { supported: false, onDevice: false, reason: "Dictation is not available here." };
  }
}

export async function start(handlers: DictationHandlers): Promise<DictationSession | null> {
  const can = await capability();
  if (!can.supported) {
    handlers.onError?.(can.reason ?? "Dictation is not available here.");
    return null;
  }
  const permission = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
  if (!permission.granted) {
    handlers.onError?.("The microphone is blocked for CashKit.");
    return null;
  }

  const resultSub = ExpoSpeechRecognitionModule.addListener("result", (event) => {
    const first = event.results[0];
    if (first) handlers.onResult(first.transcript.trim(), Boolean(event.isFinal));
  });
  const errorSub = ExpoSpeechRecognitionModule.addListener("error", () => {
    handlers.onError?.("Dictation stopped.");
  });
  const endSub = ExpoSpeechRecognitionModule.addListener("end", () => {
    handlers.onEnd?.();
  });

  ExpoSpeechRecognitionModule.start({
    lang: LANG,
    interimResults: true,
    continuous: true,
    // The §9 rule, as a flag. Nothing leaves the device to be transcribed.
    requiresOnDeviceRecognition: true,
  });

  return {
    stop: () => {
      ExpoSpeechRecognitionModule.stop();
      resultSub.remove();
      errorSub.remove();
      endSub.remove();
    },
  };
}
