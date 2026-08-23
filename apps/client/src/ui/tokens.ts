/**
 * The computed-receipt design language, as tokens (ADR-0023).
 *
 * Values are lifted from `design.pen` at revision 9bdb617, which is the
 * reference for screens 1–5. Three type families do three jobs and never swap:
 * a serif display face for figures the user came to read, a grotesque for UI
 * text, and a monospace reserved for **provenance** — as-of stamps, item ids,
 * revisions, engine version. Monospace in this app means "this is a fact about
 * where the number came from", so it is never used for decoration.
 */
export const color = {
  paper: "#F6F5F0",
  ink: "#1C201D",
  sub: "#6A6F69",
  faint: "#A8ACA3",
  hair: "#E5E3D9",
  hairSoft: "#F0EEE6",
  dotted: "#CBC8BB",
  pine: "#0B5A48",
  pineTint: "#EBF2EE",
  rust: "#A5471D",
  card: "#FFFFFF",
  areaFill: "#0B5A4812",
  recordedBand: "#1C201D09",
  grid: "#EFEDE4",
  recordedLine: "#9BA09A",
} as const;

export const font = {
  display: "Newsreader, Georgia, 'Times New Roman', serif",
  ui: "'Schibsted Grotesk', -apple-system, 'Helvetica Neue', Arial, sans-serif",
  mono: "'Fragment Mono', ui-monospace, 'SF Mono', Menlo, monospace",
} as const;

export const space = {
  screenX: 20,
  screenTop: 64,
  screenBottom: 28,
  card: 18,
  row: 8,
} as const;

export const radius = { card: 8, pill: 999 } as const;
