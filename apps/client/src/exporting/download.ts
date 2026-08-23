/**
 * Saving the exported workbook — the web half (SPEC §6-S14).
 *
 * `GET /export` needs the bearer, so the file cannot be a plain link: it is
 * fetched, turned into a blob, and handed to the browser through an object URL
 * that is revoked immediately afterwards. Nothing here reads the bytes — the
 * workbook is the service's, and the client is a courier.
 *
 * The native half is `download.native.ts`, which writes the bytes to the app's
 * cache and opens the OS share sheet.
 */
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

/** How the export is delivered on this platform, for the button's label. */
export const DELIVERY: "download" | "share" = "download";

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

export async function saveExport(request: ExportRequest): Promise<ExportResult> {
  const session = await loadSession();
  const response = await fetch(`${API_BASE_URL}/export?${exportQuery(request)}`, {
    headers: session ? { Authorization: `Bearer ${session.token}` } : {},
  });
  if (!response.ok) {
    return { ok: false, error: `The export could not be produced (${response.status}).` };
  }
  const blob = await response.blob();
  const filename = exportFilename(request);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.setAttribute("data-testid", "export-download-anchor");
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
  return { ok: true, filename };
}
