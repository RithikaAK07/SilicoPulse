"use client";
import { useMemo } from "react";
import { RotateCcw } from "lucide-react";
import { useMeta } from "@/lib/api";
import { useFilters } from "@/lib/store";
import { rangeFill } from "@/lib/utils";
import { Card } from "./ui/card";
import { Button } from "./ui/button";
import { Select } from "./ui/select";

/** Global dashboard filters (shared by the dashboard, Evidence page and Copilot). */
export function FilterBar({ compact = false }: { compact?: boolean }) {
  const { data: meta } = useMeta();
  const f = useFilters();
  const days = useMemo(() => {
    if (!meta) return [] as string[];
    const out: string[] = [];
    const d = new Date(meta.filters.date_min + "T00:00:00Z");
    const end = new Date(meta.filters.date_max + "T00:00:00Z");
    while (d <= end) {
      out.push(d.toISOString().slice(0, 10));
      d.setUTCDate(d.getUTCDate() + 1);
    }
    return out;
  }, [meta]);
  if (!meta) return null;
  const fromIdx = f.dateFrom ? days.indexOf(f.dateFrom) : 0;
  const toIdx = f.dateTo ? days.indexOf(f.dateTo) : days.length - 1;
  return (
    <Card className={compact ? "mb-3 p-2.5" : "mb-4 p-3"}>
      <div className={compact ? "grid gap-2 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end" : "grid gap-3 md:grid-cols-2 xl:grid-cols-[1fr_1fr_1fr_2fr_auto] xl:items-end"}>
        <Labeled label="Environment">
          <Select value={f.environment} placeholder="All environments" options={meta.filters.environment} onValueChange={(v) => f.set({ environment: v })} />
        </Labeled>
        <Labeled label="Hardware">
          <Select value={f.hardware} placeholder="All hardware" options={meta.filters.hardware} onValueChange={(v) => f.set({ hardware: v })} />
        </Labeled>
        <Labeled label="Workload pattern">
          <Select value={f.workload} placeholder="All workloads" options={meta.filters.workload} onValueChange={(v) => f.set({ workload: v })} />
        </Labeled>
        {!compact && (
        <Labeled label={`Date range · ${days[fromIdx]} → ${days[toIdx]}`}>
          <div className="grid grid-cols-2 gap-3 pt-2">
            <input
              type="range"
              className="range w-full"
              style={rangeFill(fromIdx, 0, days.length - 1)}
              aria-label="Start date"
              min={0}
              max={days.length - 1}
              value={fromIdx}
              onChange={(e) => {
                const i = Math.min(+e.target.value, toIdx);
                f.set({ dateFrom: i === 0 ? "" : days[i] });
              }}
            />
            <input
              type="range"
              className="range w-full"
              style={rangeFill(toIdx, 0, days.length - 1)}
              aria-label="End date"
              min={0}
              max={days.length - 1}
              value={toIdx}
              onChange={(e) => {
                const i = Math.max(+e.target.value, fromIdx);
                f.set({ dateTo: i === days.length - 1 ? "" : days[i] });
              }}
            />
          </div>
        </Labeled>
        )}
        <Button variant="ghost" size="sm" onClick={f.reset}>
          <RotateCcw className="h-4 w-4" /> Reset
        </Button>
      </div>
    </Card>
  );
}

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-2xs font-medium uppercase tracking-wide text-grey-500">{label}</span>
      {children}
    </label>
  );
}
