/**
 * Picking a workbook — the web half of the SPEC §2.1 platform adapter.
 *
 * A real `<input type="file">` is mounted into the document rather than
 * conjured on click, for two reasons. A browser only opens the picker inside a
 * user gesture, and an element that exists is an element a test can hand a file
 * to — `page.setInputFiles(...)` needs one, and a test that has to intercept a
 * file-chooser dialog is testing the dialog. It is visually hidden, not
 * `display: none`, because a hidden input still has to be clickable.
 *
 * The native half is `filePicker.native.ts`: it does nothing, because SPEC
 * §6-S14 points mobile at the web app for import in the MLP.
 */

/** Whether this platform can pick a workbook at all (SPEC §6-S14). */
export const CAN_PICK_FILE = true;

export const WORKBOOK_ACCEPT =
  ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

/**
 * Mount the hidden input and return the function that removes it again.
 *
 * The input's value is cleared after every pick so choosing the same file twice
 * in a row still fires `change`.
 */
export function mountFileInput(testID: string, onFile: (file: File) => void): () => void {
  if (typeof document === "undefined") return () => undefined;
  const input = document.createElement("input");
  input.type = "file";
  input.accept = WORKBOOK_ACCEPT;
  input.setAttribute("data-testid", testID);
  input.setAttribute("aria-hidden", "true");
  Object.assign(input.style, {
    position: "absolute",
    width: "1px",
    height: "1px",
    opacity: "0",
    pointerEvents: "none",
  });
  const handle = (): void => {
    const file = input.files?.[0];
    input.value = "";
    if (file) onFile(file);
  };
  input.addEventListener("change", handle);
  document.body.appendChild(input);
  return () => {
    input.removeEventListener("change", handle);
    input.remove();
  };
}

/** Open the mounted picker. Called from the button's press handler. */
export function openFileInput(testID: string): void {
  if (typeof document === "undefined") return;
  const input = document.querySelector(`input[data-testid="${testID}"]`);
  if (input instanceof HTMLInputElement) input.click();
}
