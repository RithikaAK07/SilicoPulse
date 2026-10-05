"use client";
import { CategoryScale, Chart as ChartJS, Filler, Legend, LineElement, LinearScale, PointElement, Tooltip } from "chart.js";
import { Line } from "react-chartjs-2";
import { INK, STATUS } from "@/lib/utils";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip, Legend);

/** One drifting random variable over time (mean + p95 band) against its risk threshold. Chart.js renders the dense series. */
export function DriftChart({ series, meanKey, p95Key, threshold, color, unit }: { series: any[]; meanKey: string; p95Key: string; threshold: number; color: string; unit: string }) {
  const labels = series.map((s) => s.day);
  const above = series.map((s) => (s[meanKey] > threshold ? s[meanKey] : null));
  return (
    <Line
      data={{
        labels,
        datasets: [
          { label: "Daily mean", data: series.map((s) => s[meanKey]), borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, tension: 0.3 },
          { label: "Daily p95", data: series.map((s) => s[p95Key]), borderColor: `${color}99`, borderDash: [4, 3], borderWidth: 1.5, pointRadius: 0, tension: 0.3 },
          { label: "Mean above limit", data: above, borderColor: "transparent", backgroundColor: STATUS.critical, pointRadius: 3, pointHoverRadius: 5, showLine: false },
          { label: `Drift limit (${threshold}${unit})`, data: labels.map(() => threshold), borderColor: STATUS.critical, borderWidth: 1, borderDash: [6, 4], pointRadius: 0 },
        ],
      }}
      options={{
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: INK.secondary, boxWidth: 10, boxHeight: 10, font: { size: 11 } } },
          tooltip: {
            backgroundColor: "#ffffffee",
            borderColor: "#334155",
            borderWidth: 1,
            titleColor: INK.primary,
            bodyColor: INK.secondary,
            callbacks: { label: (c) => (c.raw == null ? "" : `${c.dataset.label}: ${(c.raw as number).toFixed(2)}${unit}`) },
          },
        },
        scales: {
          x: { ticks: { color: INK.muted, maxTicksLimit: 8, font: { size: 10 } }, grid: { display: false }, border: { color: INK.axis } },
          y: { ticks: { color: INK.muted, font: { size: 10 } }, grid: { color: INK.grid }, border: { display: false } },
        },
      }}
    />
  );
}
