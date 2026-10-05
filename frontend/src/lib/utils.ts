import type React from "react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Design-token palette (mirrors globals.css / tailwind.config). */
export const TOKENS = {
  white: "#FFFFFF", grey50: "#F7F7F9", grey100: "#F1F1F4", grey150: "#ECECEF", grey200: "#E7E7EB", grey300: "#D4D4DA",
  grey400: "#A1A1AA", grey500: "#71717A", grey600: "#52525B", grey800: "#27272A", black: "#0E0E10", black2: "#1C1C20",
  red50: "#FFF1F1", red100: "#FEE2E2", red400: "#F87171", red500: "#EF4444", red600: "#DC2626", red700: "#B91C1C", red900: "#7F1D1D",
};

/** Categorical series order. Assign in order, never cycle; charts use at most 5. */
export const SERIES = [TOKENS.black, TOKENS.red600, TOKENS.grey600, TOKENS.grey400, TOKENS.red900, TOKENS.grey300];
/** Status roles (always paired with a label or icon; no green/amber). */
export const STATUS = { good: TOKENS.black, warning: TOKENS.grey500, serious: TOKENS.red700, critical: TOKENS.red600 };
/** Status colours for text on white (all >= 4.5:1). */
export const STATUS_INK = { good: TOKENS.black, warning: TOKENS.grey600, serious: TOKENS.red700, critical: TOKENS.red700 };
export const INK = { primary: TOKENS.black, secondary: TOKENS.grey600, muted: TOKENS.grey500, grid: TOKENS.grey150, axis: TOKENS.grey300, surface: TOKENS.white };
/** Single-series bars: grey by default, the highlighted (top) item in red. */
export const BAR = { base: TOKENS.grey600, highlight: TOKENS.red600 };
/** Diverging scale (SHAP / deltas). */
export const DIVERGING = { raises: TOKENS.red600, lowers: TOKENS.black };

/** Sequential scale: white -> red-100 -> red-600 -> red-900. */
const SEQ_STOPS: [number, [number, number, number]][] = [
  [0, [255, 255, 255]], [0.33, [254, 226, 226]], [0.72, [220, 38, 38]], [1, [127, 29, 29]],
];
export function seqColor(t: number) {
  const x = Math.max(0, Math.min(1, t));
  for (let i = 1; i < SEQ_STOPS.length; i++) {
    const [t1, c1] = SEQ_STOPS[i];
    const [t0, c0] = SEQ_STOPS[i - 1];
    if (x <= t1) {
      const f = (x - t0) / (t1 - t0 || 1);
      const c = c0.map((v, j) => Math.round(v + (c1[j] - v) * f));
      return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
    }
  }
  return "rgb(127, 29, 29)";
}
/** Text colour that stays readable on a sequential cell. */
export const seqText = (t: number) => (t > 0.55 ? TOKENS.white : TOKENS.black);

/** Inline style for the .range slider black fill. */
export const rangeFill = (value: number, min: number, max: number) =>
  ({ ["--fill" as string]: `${max > min ? ((value - min) / (max - min)) * 100 : 0}%` }) as React.CSSProperties;

export const pct = (x: number | null | undefined, d = 1) => (x == null ? "–" : `${(x * 100).toFixed(d)}%`);
export const num = (x: number | null | undefined, d = 0) =>
  x == null ? "–" : x.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });

/** Risk levels (no green/amber): Low = white/grey border, Moderate = grey, High = red tint, Critical = solid red.
 *  `color` is the fill used for dots, gauges and nodes; `onColor` the text drawn on it; `ink` text on white. */
export function riskStatus(r: number): { label: string; color: string; ink: string; onColor: string; badge: string } {
  if (r < 0.1) return { label: "Low", color: TOKENS.grey400, ink: TOKENS.black, onColor: TOKENS.black, badge: "border-grey-300 bg-white text-black" };
  if (r < 0.25) return { label: "Moderate", color: TOKENS.grey600, ink: TOKENS.grey600, onColor: TOKENS.white, badge: "border-grey-200 bg-grey-100 text-black" };
  if (r < 0.45) return { label: "High", color: TOKENS.red400, ink: TOKENS.red700, onColor: TOKENS.black, badge: "border-red-200 bg-red-100 text-red-900" };
  return { label: "Critical", color: TOKENS.red600, ink: TOKENS.red700, onColor: TOKENS.white, badge: "border-red-600 bg-red-600 text-white" };
}

function download(name: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function exportCSV(name: string, rows: Record<string, unknown>[]) {
  if (!rows.length) return;
  const cols = Object.keys(rows[0]);
  const esc = (v: unknown) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  download(name, [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n"), "text/csv");
}

export function exportJSON(name: string, data: unknown) {
  download(name, JSON.stringify(data, null, 2), "application/json");
}
