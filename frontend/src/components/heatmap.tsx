"use client";
import { useState } from "react";
import { pct, seqColor, seqText } from "@/lib/utils";

interface Cell {
  row: string;
  col: string;
  fail_rate: number;
  runs: number;
}

/** Sequential single-hue heatmap with per-cell hover readout. */
export function Heatmap({ rows, cols, cells, rowLabel }: { rows: string[]; cols: string[]; cells: Cell[]; rowLabel: string }) {
  const [hover, setHover] = useState<Cell | null>(null);
  const map = new Map(cells.map((c) => [`${c.row}|${c.col}`, c]));
  const hi = Math.max(0.01, ...cells.map((c) => c.fail_rate));
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full border-separate" style={{ borderSpacing: 2 }}>
          <thead>
            <tr>
              <th className="pr-2 text-left text-2xs font-medium uppercase text-grey-500">{rowLabel}</th>
              {cols.map((c) => (
                <th key={c} className="text-center text-2xs font-medium text-grey-500">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r}>
                <td className="pr-2 font-mono text-2xs text-grey-800">{r}</td>
                {cols.map((c) => {
                  const cell = map.get(`${r}|${c}`);
                  const t = cell ? cell.fail_rate / hi : 0;
                  return (
                    <td
                      key={c}
                      onMouseEnter={() => cell && setHover(cell)}
                      onMouseLeave={() => setHover(null)}
                      className="h-6 min-w-[3rem] rounded-[3px] text-center text-2xs tabular-nums hover:outline hover:outline-2 hover:outline-grey-800"
                      style={{ background: cell ? seqColor(t) : "#F7F7F9", color: seqText(t) }}
                    >
                      {cell && t > 0.45 ? pct(cell.fail_rate, 0) : ""}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-grey-500">
        <span className="min-h-4">
          {hover ? (
            <>
              {rowLabel} <b className="text-black">{hover.row}</b> · band <b className="text-black">{hover.col}</b> · failure rate{" "}
              <b className="text-black">{pct(hover.fail_rate)}</b> over {hover.runs} runs
            </>
          ) : (
            "Hover a cell for details"
          )}
        </span>
        <span className="flex items-center gap-2">
          0%
          <span className="h-2 w-28 rounded" style={{ background: `linear-gradient(90deg, ${seqColor(0)}, ${seqColor(0.5)}, ${seqColor(1)})` }} />
          {pct(hi, 0)} failure rate
        </span>
      </div>
    </div>
  );
}
