import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Categorical slots (light steps, validated for CVD separation on the white surface). Assign in order, never cycle. */
export const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
/** Reserved status colors — always paired with a label or icon. */
export const STATUS = { good: "#0ca30c", warning: "#fab219", serious: "#ec835a", critical: "#d03b3b" };
/** Darker steps of the status hues for text on light surfaces (status fills stay as above). */
export const STATUS_INK = { good: "#006300", warning: "#8a5a00", serious: "#b4471d", critical: "#b42323" };
export const INK = { primary: "#0f172a", secondary: "#475569", muted: "#64748b", grid: "#e2e8f0", axis: "#cbd5e1", surface: "#ffffff" };

/** Sequential single-hue ramp (blue), light (near zero, recedes into the surface) -> dark (high). */
const SEQ = ["#eef4fc", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#0d366b"];
export function seqColor(t: number) {
  const i = Math.max(0, Math.min(SEQ.length - 1, Math.round(t * (SEQ.length - 1))));
  return SEQ[i];
}

export const pct = (x: number | null | undefined, d = 1) => (x == null ? "–" : `${(x * 100).toFixed(d)}%`);
export const num = (x: number | null | undefined, d = 0) =>
  x == null ? "–" : x.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });

export function riskStatus(r: number): { label: string; color: string; ink: string } {
  if (r < 0.1) return { label: "Low", color: STATUS.good, ink: STATUS_INK.good };
  if (r < 0.25) return { label: "Moderate", color: STATUS.warning, ink: STATUS_INK.warning };
  if (r < 0.45) return { label: "High", color: STATUS.serious, ink: STATUS_INK.serious };
  return { label: "Critical", color: STATUS.critical, ink: STATUS_INK.critical };
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
