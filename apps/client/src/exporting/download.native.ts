/**
 * Saving the exported workbook — the native half: the OS share sheet.
 *
 * SPEC §6-S14: *"Mobile: export via share sheet."* A phone has no download
 * folder the user meaningfully browses, so the workbook is written to the
 * app's cache directory and handed to the system sharer, which is how a file
 * leaves an iOS or Android app.
 *
 * **Never run on a device.** There is no simulator on the build machine (S3
 * handoff §6), so this path is typechecked and not executed; it belongs to the
 * same S6 device pass as dictation (D-MLP-48). It is written rather than
 * stubbed because a stub would have to be replaced blind.
 */
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";

import { API_BASE_URL } from "../api/client";
import { loadSession } from "../api/tokenStore";

export interface ExportRequest {
  mode: "budget" | "ledger";
  months: number;
  start?: string;
  scenario?: string;
}

export interface ExportResult {
  ok: boolean;
  filename?: string;
  error?: string;
}

export const DELIVERY: "download" | "share" = "share";

export function exportQuery(request: ExportRequest): string {
  const params = new URLSearchParams({
    mode: request.mode,
    months: String(request.months),
  });
  if (request.start) params.set("start", request.start);
  if (request.scenario) params.set("scenario", request.scenario);
  return params.toString();
}

export function exportFilename(request: ExportRequest): string {
  return `cashkit-${request.mode}.xlsx`;
}

const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

export async function saveExport(request: ExportRequest): Promise<ExportResult> {
  if (!(await Sharing.isAvailableAsync())) {
    return { ok: false, error: "This device cannot share files." };
  }
  const session = await loadSession();
  const filename = exportFilename(request);
  const target = `${FileSystem.cacheDirectory ?? ""}${filename}`;
  const result = await FileSystem.downloadAsync(
    `${API_BASE_URL}/export?${exportQuery(request)}`,
    target,
    { headers: session ? { Authorization: `Bearer ${session.token}` } : {} },
  );
  if (result.status !== 200) {
    return { ok: false, error: `The export could not be produced (${result.status}).` };
  }
  await Sharing.shareAsync(result.uri, { mimeType: XLSX, dialogTitle: "Export" });
  return { ok: true, filename };
}
