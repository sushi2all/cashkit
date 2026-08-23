/**
 * Dictation — web (and the default).
 *
 * SPEC §9 makes the speech path a compliance question, not a feature note:
 * *prefer on-device recognition; any server-side speech provider joins the
 * subprocessor list or dictation ships disabled on that platform.*
 *
 * The browser's Web Speech API does not say where recognition happens, and in
 * the common case (Chrome) it happens on Google's servers. So this adapter
 * fails closed (D-MLP-45):
 *
 *  1. It asks for **on-device** recognition wherever the browser can offer it
 *     (`SpeechRecognition.available({processLocally: true})`, Chromium 138+).
 *  2. When only cloud recognition is on offer, dictation stays **disabled**
 *     unless the build explicitly enables it with
 *     `EXPO_PUBLIC_ALLOW_CLOUD_DICTATION=1` — which S6 may only set once the
 *     browser speech vendor is named on the privacy page's subprocessor list.
 *     The default build ships web dictation on-device or not at all.
 *
 * The audio never reaches the CashKit service either way: what the service
 * receives is the same text the user could have typed.
 */
export type DictationState = "idle" | "listening" | "unsupported" | "denied" | "error";

export interface DictationCapability {
  /** Can this platform dictate at all, under the §9 rule? */
  supported: boolean;
  /** Does recognition stay on the device? */
  onDevice: boolean;
  /** Why dictation is unavailable, when it is. */
  reason?: string;
}

export interface DictationHandlers {
  /** Called with the running transcript, partial results included. */
  onResult: (transcript: string, isFinal: boolean) => void;
  onError?: (message: string) => void;
  onEnd?: () => void;
}

export interface DictationSession {
  stop: () => void;
}

const ALLOW_CLOUD = process.env.EXPO_PUBLIC_ALLOW_CLOUD_DICTATION === "1";

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  processLocally?: boolean;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: unknown) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onend: (() => void) | null;
}

interface SpeechRecognitionCtor {
  new (): SpeechRecognitionLike;
  available?: (options: { langs: string[]; processLocally: boolean }) => Promise<string>;
}

function ctor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export async function capability(): Promise<DictationCapability> {
  const Recognition = ctor();
  if (!Recognition) {
    return { supported: false, onDevice: false, reason: "This browser cannot dictate." };
  }
  let onDevice = false;
  if (typeof Recognition.available === "function") {
    try {
      const status = await Recognition.available({ langs: ["en-GB"], processLocally: true });
      onDevice = status === "available" || status === "downloadable";
    } catch {
      onDevice = false;
    }
  }
  if (!onDevice && !ALLOW_CLOUD) {
    return {
      supported: false,
      onDevice: false,
      // The §9 sentence, said to the user rather than to the auditor.
      reason: "This browser sends dictation to its vendor to transcribe it, so it is off here. Type instead, or use the app.",
    };
  }
  return { supported: true, onDevice };
}

export async function start(handlers: DictationHandlers): Promise<DictationSession | null> {
  const can = await capability();
  if (!can.supported) {
    handlers.onError?.(can.reason ?? "Dictation is not available here.");
    return null;
  }
  const Recognition = ctor();
  if (!Recognition) return null;

  const recognition = new Recognition();
  recognition.lang = "en-GB";
  recognition.continuous = true;
  recognition.interimResults = true;
  if (can.onDevice) recognition.processLocally = true;

  recognition.onresult = (event: unknown) => {
    const e = event as { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> };
    let text = "";
    let isFinal = false;
    for (let i = 0; i < e.results.length; i += 1) {
      const result = e.results[i];
      if (!result) continue;
      const alternative = result[0];
      if (alternative) text += alternative.transcript;
      if (result.isFinal) isFinal = true;
    }
    handlers.onResult(text.trim(), isFinal);
  };
  recognition.onerror = (event: unknown) => {
    const code = (event as { error?: string }).error ?? "error";
    handlers.onError?.(
      code === "not-allowed" ? "The microphone is blocked for this site." : "Dictation stopped.",
    );
  };
  recognition.onend = () => handlers.onEnd?.();

  recognition.start();
  return { stop: () => recognition.stop() };
}
