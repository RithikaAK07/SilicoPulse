"use client";
/** Data source selector + dataset-aware Scenario Lab for the Dataset Generator page. */
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Activity, CheckCircle2, Database, Download, FileSpreadsheet, FlaskConical, Info, Lock } from "lucide-react";
import { api, download, post, type ActiveDatasetEntry, type ActiveDatasets } from "@/lib/api";
import { cn, num } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { ErrorState, Spinner } from "./common";

const ROLE_LABEL: Record<string, string> = {
  timestamp: "Timestamp", telemetry: "Telemetry", context: "Context", log: "Log", outcome: "Outcome", performance: "Performance",
  config: "Config", random: "Randomized", seed: "Seed", run_id: "Run ID", config_id: "Config ID", environment: "Environment",
  hardware: "Hardware", workload: "Workload", error_signature: "Error signature", other: "Other",
};

/** DATA SOURCE: Benchmark | Uploaded. Selecting one makes it the app-wide active dataset. */
export function DataSourceSelector({ data, busy, canSwitch, onSelect }: {
  data: ActiveDatasets; busy: string | null; canSwitch: boolean; onSelect: (id: ActiveDatasetEntry["dataset_id"]) => void;
}) {
  const bench = data.datasets.find((d) => d.dataset_id === "benchmark");
  const uploads = data.datasets.filter((d) => d.source === "uploaded");
  const uploadedActive = data.active.source === "uploaded";
  return (
    <Card className="mb-6">
      <CardHeader>
        <div>
          <CardTitle>Data source</CardTitle>
          <CardDescription>
            The selected dataset becomes the active dataset for every view. Uploaded files are reused from CSV Data Upload, so there is no need to upload them again.
          </CardDescription>
        </div>
        {!canSwitch && (
          <span className="inline-flex items-center gap-1.5 text-xs text-grey-600"><Lock className="h-3.5 w-3.5" /> Read-only role: the data source can't be changed</span>
        )}
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 md:grid-cols-2" role="group" aria-label="Data source">
          <button
            aria-pressed={!uploadedActive}
            disabled={!canSwitch || !!busy}
            onClick={() => uploadedActive && onSelect("benchmark")}
            className="sp-sel flex items-start gap-3 rounded-xl border border-grey-300 bg-white p-4 text-left disabled:cursor-not-allowed"
          >
            <Database className="mt-0.5 h-5 w-5 shrink-0" strokeWidth={1.75} />
            <span className="min-w-0">
              <span className="block text-sm font-semibold">Benchmark Dataset</span>
              <span className={cn("block text-xs", uploadedActive ? "text-grey-600" : "text-white/85")}>
                Synthetic execution campaign{bench ? ` · ${num(bench.row_count)} executions` : ""}
              </span>
              {busy === "benchmark" && <span className="mt-1 block text-xs"><Spinner label="Switching & retraining…" /></span>}
            </span>
          </button>
          <div
            role="group"
            aria-label="Uploaded Dataset"
            data-active={uploadedActive ? "true" : "false"}
            className={cn("sp-sel rounded-xl border border-grey-300 bg-white p-4", !uploads.length && "opacity-60")}
          >
            <div className="flex items-start gap-3">
              <FileSpreadsheet className="mt-0.5 h-5 w-5 shrink-0" strokeWidth={1.75} />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold">Uploaded Dataset</div>
                {!uploads.length ? (
                  <div className="text-xs text-grey-600">No uploaded dataset available. Upload a file in CSV Data Upload first.</div>
                ) : (
                  <div className="mt-2 space-y-2">
                    {uploads.map((u) => {
                      const on = u.dataset_id === data.active_id;
                      return (
                        <button
                          key={u.dataset_id}
                          disabled={!canSwitch || !!busy || on}
                          onClick={() => onSelect(u.dataset_id)}
                          aria-label={`Use ${u.dataset_name}`}
                          className={cn(
                            "block w-full rounded-lg border px-3 py-2 text-left text-xs",
                            uploadedActive ? (on ? "border-white/70 bg-white/10" : "border-white/30 hover:border-white/70") : "border-grey-300 hover:border-grey-400",
                            "disabled:cursor-default",
                          )}
                        >
                          <span className="block font-mono text-sm [overflow-wrap:anywhere]">{u.dataset_name}</span>
                          <span className={cn("block", uploadedActive ? "text-white/85" : "text-grey-600")}>
                            Type: {u.type_label} · Rows: {num(u.row_count)} · Columns: {u.column_count}{on ? " · active" : ""}
                          </span>
                          {busy === u.dataset_id && <Spinner label="Switching…" />}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function FieldChips({ entry }: { entry: ActiveDatasetEntry }) {
  const [all, setAll] = useState(false);
  const real = entry.detected_fields.filter((f) => !f.derived);
  const shown = all ? real : real.slice(0, 24);
  return (
    <div>
      <div className="flex flex-wrap gap-1.5">
        {shown.map((f) => (
          <span key={f.name} className="inline-flex items-center gap-1.5 rounded-md border border-grey-200 bg-grey-50 px-2 py-0.5 text-xs">
            <span className="font-mono text-grey-800">{f.name}</span>
            <span className="text-grey-500">{ROLE_LABEL[f.role] ?? f.role}{f.unit && f.unit !== "unit unknown" ? ` · ${f.unit}` : ""}</span>
          </span>
        ))}
      </div>
      {real.length > 24 && (
        <button className="mt-2 text-xs font-semibold text-red-700 hover:text-red-900" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${real.length} fields`}
        </button>
      )}
      {entry.derived_columns.length > 0 && (
        <p className="mt-2 text-2xs text-grey-500">Not in the uploaded file (derived by the execution pipeline, never used by scenarios): {entry.derived_columns.join(", ")}</p>
      )}
    </div>
  );
}

/** Uploaded-dataset mode: detected structure + scenarios enabled from the detected fields. */
export function ScenarioLab({ entry, canGenerate }: { entry: ActiveDatasetEntry; canGenerate: boolean }) {
  const cat = useQuery({ queryKey: ["scenarios", entry.dataset_id, entry.row_count], queryFn: () => api<any>(`/api/scenarios?dataset_id=${entry.dataset_id}`) });
  const [scenario, setScenario] = useState<string | null>(null);
  const [intensity, setIntensity] = useState<"low" | "medium" | "high">("medium");
  const [seed, setSeed] = useState(7);
  const Icon = entry.pipeline === "telemetry" ? Activity : FileSpreadsheet;
  const options: any[] = cat.data?.scenarios ?? [];
  // default: the first scenario this dataset supports (derived, not stored)
  const selected = scenario && options.some((s) => s.id === scenario && s.enabled) ? scenario : options.find((s) => s.enabled)?.id ?? null;
  const gen = useMutation({ mutationFn: () => post<any>("/api/scenarios/generate", { dataset_id: entry.dataset_id, scenario: selected, intensity, seed }) });
  const r = gen.data;
  const cols: string[] = r?.preview?.[0] ? Object.keys(r.preview[0]).slice(0, 12) : [];

  return (
    <div className="grid gap-6">
      <div className="grid gap-6 xl:grid-cols-[1fr_1.4fr]">
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2"><Icon className="h-5 w-5" strokeWidth={1.75} /> Uploaded dataset</CardTitle>
              <CardDescription>Detected structure (from preprocessing). Scenarios use it as the baseline; the dataset itself is never modified.</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <dl className="grid grid-cols-2 gap-2 text-sm">
              {[["Dataset", entry.dataset_name], ["Type", entry.type_label], ["Rows", num(entry.row_count)], ["Columns", String(entry.column_count)]].map(([k, v]) => (
                <div key={k} className="rounded-lg border border-grey-200 p-3">
                  <dt className="text-2xs text-grey-500">{k}</dt>
                  <dd className="font-semibold [overflow-wrap:anywhere]">{v}</dd>
                </div>
              ))}
            </dl>
            {entry.channels && <div className="text-xs text-grey-600">{entry.channels.length} telemetry channel{entry.channels.length === 1 ? "" : "s"} detected: {entry.channels.map((c) => `${c.name} (${c.unit})`).join(", ")}</div>}
            <div>
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-grey-500">Detected fields</div>
              <FieldChips entry={entry} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2"><FlaskConical className="h-5 w-5" strokeWidth={1.75} /> Scenario Lab</CardTitle>
              <CardDescription>
                {entry.pipeline === "telemetry"
                  ? "Telemetry scenarios modify only the selected channel. No PASS/FAIL, seed, configuration or error fields are created."
                  : "Execution scenarios are offered only when the fields they need exist in this dataset."}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            {cat.isLoading ? <Spinner label="Detecting available scenarios…" /> : cat.isError ? <ErrorState error={cat.error} /> : !cat.data.supported ? (
              <p className="text-sm text-grey-600">{cat.data.message}</p>
            ) : (
              <>
                {cat.data.message && <p className="mb-3 text-sm text-grey-600">{cat.data.message}</p>}
                <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Scenario">
                  {options.map((s) => (
                    <button
                      key={s.id}
                      role="radio"
                      aria-checked={selected === s.id}
                      aria-pressed={selected === s.id}
                      disabled={!s.enabled}
                      onClick={() => setScenario(s.id)}
                      className={cn("sp-sel rounded-lg border border-grey-300 bg-white p-3 text-left disabled:cursor-not-allowed disabled:opacity-55")}
                    >
                      <span className="block text-sm font-semibold">{s.label}</span>
                      <span className={cn("block text-xs", selected === s.id ? "text-white/85" : "text-grey-600")}>
                        {s.enabled ? `${s.description} Field${s.fields.length === 1 ? "" : "s"}: ${s.fields.join(", ")}` : `Unavailable: ${s.reason}`}
                      </span>
                    </button>
                  ))}
                </div>
                <div className="mt-4 flex flex-wrap items-end gap-4">
                  <div>
                    <div className="mb-1 text-2xs font-medium uppercase tracking-wide text-grey-500">Intensity</div>
                    <div className="flex gap-1">
                      {(["low", "medium", "high"] as const).map((k) => (
                        <button key={k} aria-pressed={intensity === k} onClick={() => setIntensity(k)}
                          className="sp-sel h-8 rounded-full border border-grey-300 bg-white px-3.5 text-xs font-semibold capitalize text-black aria-pressed:bg-transparent">
                          {k}
                        </button>
                      ))}
                    </div>
                  </div>
                  <label className="block w-32">
                    <span className="mb-1 block text-2xs font-medium uppercase tracking-wide text-grey-500">Scenario seed</span>
                    <input type="number" min={0} max={999999} value={seed} onChange={(e) => setSeed(Math.max(0, Math.min(999999, +e.target.value || 0)))}
                      className="h-10 w-full rounded-[10px] border border-grey-300 bg-white px-3 text-sm tabular-nums hover:border-grey-400 focus:border-red-600 focus:shadow-focus focus:outline-none" />
                  </label>
                  <Button variant="primary" size="lg" className="flex-1" disabled={!selected || gen.isPending || !canGenerate} onClick={() => gen.mutate()}>
                    {gen.isPending ? <Spinner label="Generating scenario…" /> : <><FlaskConical className="h-4 w-4" /> Generate scenario</>}
                  </Button>
                </div>
                {gen.error && <p className="mt-3 text-sm text-red-700">{(gen.error as Error).message}</p>}
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {r && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2"><CheckCircle2 className="h-5 w-5" strokeWidth={1.75} /> {r.label} · {r.intensity}</CardTitle>
              <CardDescription>
                Derived from <span className="font-mono">{r.base_dataset.dataset_name}</span> · {num(r.rows)} rows · {r.column_count} columns (same as the original) ·
                modified: {r.modified_fields.join(", ")} · seed {r.seed}
              </CardDescription>
            </div>
            <Button size="sm" onClick={() => download(`/api/scenarios/${r.scenario_id}/download`, `${r.base_dataset.dataset_name.replace(/\.[^.]+$/, "")}_${r.scenario}_${r.intensity}.csv`)}>
              <Download className="h-4 w-4" /> Download scenario CSV
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-start gap-2 rounded-lg border border-grey-200 bg-grey-50 p-3 text-xs text-grey-700">
              <Info className="mt-0.5 h-4 w-4 shrink-0" /> {r.note} Stored separately as scenario <span className="font-mono">{r.scenario_id.slice(0, 8)}</span>.
            </div>
            {r.detail?.changes ? (
              <div className="overflow-x-auto rounded-lg border border-grey-200">
                <table className="w-full min-w-[560px] text-xs">
                  <thead className="bg-grey-50 text-grey-500">
                    <tr>{["Channel", "Rows modified", "Mean before → after", "Min before → after", "Max before → after"].map((h, i) => (
                      <th key={h} className={cn("px-3 py-1.5 font-medium", i ? "text-right" : "text-left")}>{h}</th>
                    ))}</tr>
                  </thead>
                  <tbody>
                    {r.detail.changes.map((c: any) => (
                      <tr key={c.column} className="border-t border-grey-150 tabular-nums">
                        <td className="px-3 py-1 font-mono text-grey-800">{c.column}</td>
                        <td className="px-3 py-1 text-right">{num(c.rows_modified)}</td>
                        <td className="px-3 py-1 text-right">{c.mean_before.toFixed(3)} → <b>{c.mean_after.toFixed(3)}</b></td>
                        <td className="px-3 py-1 text-right">{c.min_before.toFixed(3)} → <b>{c.min_after.toFixed(3)}</b></td>
                        <td className="px-3 py-1 text-right">{c.max_before.toFixed(3)} → <b>{c.max_after.toFixed(3)}</b></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
                {[["Failures before", num(r.detail.failures_before)], ["Failures after", num(r.detail.failures_after)], ["Rows modified", num(r.detail.rows_modified)],
                  ["Target", r.detail.parameter ? `${r.detail.parameter} = ${r.detail.value}` : r.detail.parameters ? r.detail.parameters.map((p: string, i: number) => `${p}=${r.detail.values[i]}`).join(" & ")
                    : r.detail.seed_count ? `${r.detail.seed_count} seeds` : `${num(r.detail.runs_degraded)} runs, −${r.detail.throughput_drop}`]].map(([k, v]) => (
                  <div key={k} className="rounded-lg border border-grey-200 p-3"><div className="text-2xs text-grey-500">{k}</div><div className="font-semibold [overflow-wrap:anywhere]">{v}</div></div>
                ))}
              </div>
            )}
            <div className="max-h-80 overflow-auto rounded-lg border border-grey-200">
              <table className="text-2xs">
                <thead className="sticky top-0 bg-white text-grey-500">
                  <tr>{cols.map((c) => <th key={c} className={cn("whitespace-nowrap px-2.5 py-1.5 text-left font-medium", r.modified_fields.includes(c) && "text-red-700")}>{c}</th>)}</tr>
                </thead>
                <tbody className="tabular-nums">
                  {r.preview.map((row: any, i: number) => (
                    <tr key={i} className="border-t border-grey-200">
                      {cols.map((c) => (
                        <td key={c} className={cn("whitespace-nowrap px-2.5 py-1", r.modified_fields.includes(c) ? "font-semibold text-black" : "text-grey-800")}>
                          {typeof row[c] === "number" && !Number.isInteger(row[c]) ? row[c].toFixed(3) : String(row[c])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-2xs text-grey-500">Preview: first 50 rows{cols.length < r.column_count ? `, first ${cols.length} columns` : ""}; modified columns in bold.</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
