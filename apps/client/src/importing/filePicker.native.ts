/**
 * Picking a workbook — the native half, which deliberately cannot.
 *
 * SPEC §6-S14: *"Mobile: export via share sheet; import points to the web app
 * in the MLP."* That is the SPEC's own decision, not a shortcut taken here, and
 * the screen renders the pointer rather than a disabled button with no reason
 * attached. Keeping the surface identical to the web module means the screen
 * has no platform branch beyond this flag.
 */

export const CAN_PICK_FILE = false;

export const WORKBOOK_ACCEPT = ".xlsx";

export function mountFileInput(_testID: string, _onFile: (file: unknown) => void): () => void {
  return () => undefined;
}

export function openFileInput(_testID: string): void {
  // Nothing to open. The screen never calls this when CAN_PICK_FILE is false.
}
