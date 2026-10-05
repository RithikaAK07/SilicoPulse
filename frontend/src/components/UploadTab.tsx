"use client";
import { useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle, ArrowRight, CheckCircle2, Circle, Download, FileSpreadsheet, Loader2, Lock, RotateCcw, Search, Settings2, UploadCloud, X,
} from "lucide-react";
import { download, post, upload, type DatasetStatus } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { cn, num } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Select } from "./ui/select";

type Role = "config" | "random" | "telemetry" | "ignore";
interface ColumnInfo {
  name: string;
  dtype: string;
  n_unique: number;
  null_pct: number;
  sample: string[];
  role: string;
}
interface Mapping {
  outcome: string | null;
  fail_values: string[];
  performance: string | null;
  run_id: string | null;
  timestamp: string | null;
  seed: string | null;
  error_signature: string | null;
  log: string | null;
  config_id: string | null;
  environment: string | null;
  hardware: string | null;
  workload: string | null;
  roles: Record<string, Role>;
}
interface Preview {
  upload_id: string;
  filename: string;
  size_bytes: number;
  rows: number;
  columns: ColumnInfo[];
  mapping: Mapping;
  low_card_values: Record<string, string[]>;
}
type Stage = "idle" | "analyzing" | "mapping" | "ingesting" | "done" | "error";

const FIELD_KEYS = ["outcome", "performance", "run_id", "timestamp", "seed", "error_signature", "log", "config_id", "environment", "hardware", "workload"] as const;
const OPTIONAL_FIELDS: { key: (typeof FIELD_KEYS)[number]; label: string; hint: string }[] = [
  { key: "seed", label: "Random seed", hint: "enables seed repeatability (Q4)" },
  { key: "timestamp", label: "Timestamp", hint: "trend & drift charts" },
  { key: "error_signature", label: "Error signature", hint: "root-cause fingerprints (Q5)" },
  { key: "log", label: "Log / trace text", hint: "log anomaly mining & diffs" },
  { key: "run_id", label: "Run ID", hint: "execution diff selector" },
  { key: "config_id", label: "Config / profile ID", hint: "otherwise derived from settings" },
  { key: "environment", label: "Environment", hint: "filters & topology" },
  { key: "hardware", label: "Hardware", hint: "filters & topology" },
  { key: "workload", label: "Workload", hint: "filters" },
];
const ROLE_LABEL: Record<Role, string> = { config: "Config parameter", random: "Randomized variable", telemetry: "Telemetry", ignore: "Ignore" };
const NONE = "— none —";

const STEPS: { stage: Stage; label: string }[] = [
  { stage: "analyzing", label: "Analyze columns" },
  { stage: "mapping", label: "Map fields" },
  { stage: "ingesting", label: "Ingest & retrain" },
  { stage: "done", label: "Active" },
];
const ORDER: Stage[] = ["idle", "analyzing", "mapping", "ingesting", "done"];

export function UploadTab() {
  const { can, dataset, refreshDataset } = useAuth();
  const canUpload = can("upload");
  const inputRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [result, setResult] = useState<any>(null);
  const [downloading, setDownloading] = useState(false);
  const [resetting, setResetting] = useState(false);

  async function analyze(f: File) {
    if (!f.name.toLowerCase().endsWith(".csv")) {
      setError("Only .csv files are supported.");
      setStage("error");
      return;
    }
    setFile(f);
    setError(null);
    setResult(null);
    setStage("analyzing");
    try {
      const form = new FormData();
      form.append("file", f);
      const p = await upload<Preview>("/api/upload-csv/preview", form);
      setPreview(p);
      setMapping(p.mapping);
      setStage("mapping");
    } catch (e) {
      setError((e as Error).message);
      setStage("error");
    }
  }

  async function ingest() {
    if (!preview || !mapping) return;
    setStage("ingesting");
    setError(null);
    try {
      const form = new FormData();
      form.append("upload_id", preview.upload_id);
      form.append("mapping", JSON.stringify(mapping));
      const res = await upload("/api/upload-csv", form);
      setResult(res);
      setStage("done");
      await refreshDataset();
    } catch (e) {
      setError((e as Error).message);
      setStage("mapping");
    }
  }

  async function reset() {
    setResetting(true);
    try {
      await post<DatasetStatus>("/api/dataset/reset", {});
      await refreshDataset();
      setStage("idle");
      setResult(null);
      setFile(null);
    } finally {
      setResetting(false);
    }
  }

  async function sample() {
    setDownloading(true);
    try {
      await download("/api/download-sample-csv", "sandisk_execution_log_sample.csv");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDownloading(false);
    }
  }

  const busy = stage === "analyzing" || stage === "ingesting";
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader className="flex-wrap">
          <div>
            <CardTitle>Bring your own execution logs</CardTitle>
            <CardDescription>
              Upload a CSV with one row per execution. Columns are auto-classified into outcome, performance, configuration parameters, randomized variables and telemetry. You can adjust the mapping before ingesting.
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="lg" onClick={sample} disabled={downloading} className="bg-emerald-600 hover:bg-emerald-500">
              {downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <span aria-hidden>📥</span>} Download Sample SanDisk Execution CSV
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
            {dataset?.source === "uploaded" ? <FileSpreadsheet className="h-5 w-5 text-emerald-600" /> : <FileSpreadsheet className="h-5 w-5 text-slate-500" />}
            <div className="flex-1">
              <div className="font-medium text-slate-900">
                {dataset?.label ?? "…"}
                {dataset?.source === "uploaded" && dataset.filename ? ` · ${dataset.filename}` : ""}
              </div>
              <div className="text-xs text-slate-500">
                {dataset ? `${num(dataset.rows)} executions · ${dataset.config_params} config parameters · ${dataset.random_vars} randomized variables` : ""}
              </div>
            </div>
            {dataset?.source === "uploaded" && canUpload && (
              <Button variant="outline" size="sm" onClick={reset} disabled={resetting}>
                {resetting ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />} Revert to benchmark data
              </Button>
            )}
          </div>

          {!canUpload ? (
            <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900">
              <Lock className="mt-0.5 h-5 w-5 shrink-0" />
              <div>
                <div className="font-medium">Read-only role</div>
                Executive Viewers can explore every dashboard and download the sample CSV, but uploading data requires a Validation Lead or VLSI Engineer account.
              </div>
            </div>
          ) : (
            <>
              <div
                role="button"
                tabIndex={0}
                aria-label="Upload CSV file"
                onClick={() => !busy && inputRef.current?.click()}
                onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && !busy && inputRef.current?.click()}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDrag(true);
                }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDrag(false);
                  const f = e.dataTransfer.files?.[0];
                  if (f && !busy) analyze(f);
                }}
                className={cn(
                  "flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors",
                  drag ? "border-sky-500 bg-sky-50" : "border-slate-300 bg-white hover:border-sky-400 hover:bg-slate-50",
                  busy && "cursor-wait opacity-70",
                )}
              >
                <UploadCloud className={cn("h-10 w-10", drag ? "text-sky-600" : "text-slate-400")} />
                <div className="mt-3 text-sm font-medium text-slate-800">{drag ? "Drop to analyze" : "Drag & drop a CSV here, or click to browse"}</div>
                <div className="mt-1 text-xs text-slate-500">Needs a pass/fail column and at least {50} rows · up to 200 MB</div>
                <input
                  ref={inputRef}
                  type="file"
                  accept=".csv,text/csv"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) analyze(f);
                    e.target.value = "";
                  }}
                />
              </div>

              {(file || stage === "error") && (
                <div className="mt-4 rounded-xl border border-slate-200 p-4">
                  {file && (
                    <div className="mb-3 flex flex-wrap items-center gap-3">
                      <FileSpreadsheet className="h-5 w-5 text-sky-600" />
                      <span className="font-mono text-sm text-slate-800">{file.name}</span>
                      <span className="text-xs text-slate-500">{(file.size / 1024).toFixed(0)} KB</span>
                      {preview && <span className="text-xs text-slate-500">· {num(preview.rows)} rows · {preview.columns.length} columns</span>}
                      {stage === "mapping" && (
                        <Button size="sm" variant="outline" className="ml-auto" onClick={() => setStage("mapping")}>
                          <Settings2 className="h-4 w-4" /> Edit mapping
                        </Button>
                      )}
                    </div>
                  )}
                  <StepIndicator stage={stage} />
                  {error && (
                    <div className="mt-3 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {stage === "done" && result && <SuccessCard result={result} />}

      {preview && mapping && (stage === "mapping" || stage === "ingesting") && (
        <MappingDialog
          preview={preview}
          mapping={mapping}
          setMapping={setMapping}
          busy={stage === "ingesting"}
          error={error}
          onCancel={() => {
            setStage("idle");
            setPreview(null);
            setFile(null);
            setError(null);
          }}
          onConfirm={ingest}
        />
      )}
    </div>
  );
}

function StepIndicator({ stage }: { stage: Stage }) {
  const cur = ORDER.indexOf(stage === "error" ? "idle" : stage);
  return (
    <ol className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
      {STEPS.map((s, i) => {
        const idx = ORDER.indexOf(s.stage);
        const done = cur > idx || stage === "done";
        const active = cur === idx && stage !== "done";
        return (
          <li key={s.stage} className="flex items-center gap-1.5">
            {done ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
            ) : active ? (
              stage === "mapping" ? <Settings2 className="h-4 w-4 text-sky-600" /> : <Loader2 className="h-4 w-4 animate-spin text-sky-600" />
            ) : (
              <Circle className="h-4 w-4 text-slate-300" />
            )}
            <span className={cn(done ? "text-emerald-700" : active ? "font-medium text-sky-700" : "text-slate-400")}>{s.label}</span>
            {i < STEPS.length - 1 && <span className="ml-2 h-px w-6 bg-slate-200" />}
          </li>
        );
      })}
    </ol>
  );
}

function SuccessCard({ result }: { result: any }) {
  const s = result.summary;
  return (
    <Card className="border-emerald-200 bg-emerald-50/50">
      <CardContent className="flex flex-wrap items-center gap-4 pt-5">
        <CheckCircle2 className="h-8 w-8 text-emerald-600" />
        <div className="flex-1">
          <div className="font-semibold text-slate-900">Dataset ingested & models retrained</div>
          <div className="text-sm text-slate-600">
            {num(s.rows)} executions · {s.config_params} config parameters · {s.random_vars} randomized variables · {s.profiles} configuration profiles ·{" "}
            {num(s.failures)} failures. Risk model AUC <b>{result.model_auc.toFixed(3)}</b>, trained in {result.training_seconds}s.
          </div>
          {result.dataset.leakage_dropped?.length > 0 && (
            <div className="mt-1 text-xs text-amber-800">
              Dropped {result.dataset.leakage_dropped.join(", ")}: it perfectly predicts pass/fail (a leaked label), so training on it would make every analysis meaningless.
            </div>
          )}
          {result.dataset.synthetic_columns.length > 0 && (
            <div className="mt-1 text-xs text-slate-500">Not present in the CSV (neutral defaults used): {result.dataset.synthetic_columns.join(", ")}</div>
          )}
        </div>
        <Link href="/dashboard">
          <Button>
            Open dashboards <ArrowRight className="h-4 w-4" />
          </Button>
        </Link>
      </CardContent>
    </Card>
  );
}

function MappingDialog({
  preview, mapping, setMapping, busy, error, onCancel, onConfirm,
}: {
  preview: Preview;
  mapping: Mapping;
  setMapping: (m: Mapping) => void;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [query, setQuery] = useState("");
  const cols = preview.columns;
  const colNames = cols.map((c) => c.name);
  const numeric = cols.filter((c) => /int|float|decimal/i.test(c.dtype)).map((c) => c.name);
  const used = new Set(FIELD_KEYS.map((k) => mapping[k]).filter(Boolean) as string[]);
  const roleCols = cols.filter((c) => !used.has(c.name));
  const roleOf = (c: string): Role => mapping.roles[c] ?? "config";
  const counts = roleCols.reduce<Record<Role, number>>((acc, c) => ({ ...acc, [roleOf(c.name)]: acc[roleOf(c.name)] + 1 }), { config: 0, random: 0, telemetry: 0, ignore: 0 });
  const outcomeValues = mapping.outcome ? preview.low_card_values[mapping.outcome] : undefined;
  const shown = useMemo(() => roleCols.filter((c) => c.name.includes(query.toLowerCase())), [roleCols, query]);

  const setField = (key: (typeof FIELD_KEYS)[number], value: string) => {
    const v = value === NONE || value === "" ? null : value;
    const next = { ...mapping, [key]: v };
    if (key === "outcome") {
      const vals = v ? preview.low_card_values[v] ?? [] : [];
      next.fail_values = vals.filter((x) => /fail|error|abort|crash|timeout|^false$|^0$/.test(x)).slice(0, 3);
      if (!next.fail_values.length && vals.length === 2) next.fail_values = [vals[0]];
    }
    setMapping(next);
  };
  const problems: string[] = [];
  if (!mapping.outcome) problems.push("Choose the outcome (pass/fail) column.");
  else if (!outcomeValues) problems.push("The outcome column must have 12 or fewer distinct values.");
  else if (!mapping.fail_values.length) problems.push("Tick at least one value that means FAIL.");
  if (counts.config === 0) problems.push("Mark at least one column as a configuration parameter.");

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4 backdrop-blur-sm sm:items-center" role="dialog" aria-modal="true" aria-label="Column mapping">
      <div className="flex max-h-[92vh] w-full max-w-4xl flex-col rounded-2xl border border-slate-200 bg-white shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 px-6 py-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">Map CSV columns</h2>
            <p className="text-xs text-slate-500">
              <span className="font-mono">{preview.filename}</span> · {num(preview.rows)} rows · {cols.length} columns. Suggestions are auto-detected; adjust anything that looks wrong.
            </p>
          </div>
          <button onClick={onCancel} className="rounded-md p-1 text-slate-500 hover:bg-slate-100" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          <section className="grid gap-4 md:grid-cols-2">
            <div className="rounded-xl border border-sky-200 bg-sky-50/50 p-4">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-sky-800">Outcome (pass / fail) · required</div>
              <Select value={mapping.outcome ?? ""} placeholder="Select column…" options={colNames} onValueChange={(v) => setField("outcome", v)} />
              {outcomeValues && (
                <div className="mt-3">
                  <div className="mb-1.5 text-xs text-slate-600">Values that mean <b>FAIL</b>:</div>
                  <div className="flex flex-wrap gap-1.5">
                    {outcomeValues.map((v) => {
                      const on = mapping.fail_values.includes(v);
                      return (
                        <button
                          key={v}
                          onClick={() => setMapping({ ...mapping, fail_values: on ? mapping.fail_values.filter((x) => x !== v) : [...mapping.fail_values, v] })}
                          className={cn(
                            "rounded-md border px-2.5 py-1 font-mono text-xs transition-colors",
                            on ? "border-red-300 bg-red-50 text-red-800" : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50",
                          )}
                        >
                          {on ? "✕ " : ""}
                          {v || "(empty)"}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
            <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-4">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-emerald-800">Performance metric · recommended</div>
              <Select value={mapping.performance ?? NONE} options={[NONE, ...numeric]} onValueChange={(v) => setField("performance", v)} />
              <p className="mt-2 text-xs text-slate-600">Numeric metric to maximize (throughput, bandwidth, IOPS, score). Drives Pareto analysis and recommendations.</p>
            </div>
          </section>

          <section>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Optional fields</div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {OPTIONAL_FIELDS.map((f) => (
                <label key={f.key} className="block">
                  <span className="mb-1 flex items-baseline justify-between gap-2 text-xs">
                    <span className="font-medium text-slate-700">{f.label}</span>
                    <span className="truncate text-[10px] text-slate-400">{f.hint}</span>
                  </span>
                  <Select value={mapping[f.key] ?? NONE} options={[NONE, ...(f.key === "seed" ? numeric : colNames)]} onValueChange={(v) => setField(f.key, v)} />
                </label>
              ))}
            </div>
          </section>

          <section>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Remaining columns · role</div>
              <div className="flex flex-wrap gap-1.5">
                <Badge color="#2a78d6">{counts.config} config</Badge>
                <Badge color="#eb6834">{counts.random} randomized</Badge>
                <Badge color="#1baf7a">{counts.telemetry} telemetry</Badge>
                <Badge color="#94a3b8">{counts.ignore} ignored</Badge>
              </div>
            </div>
            <div className="relative mb-2">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter columns…"
                className="h-9 w-full rounded-lg border border-slate-300 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
              />
            </div>
            <div className="max-h-72 overflow-auto rounded-lg border border-slate-200">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-50 text-slate-500">
                  <tr>
                    <th className="px-3 py-1.5 text-left font-medium">Column</th>
                    <th className="px-3 py-1.5 text-left font-medium">Type</th>
                    <th className="px-3 py-1.5 text-right font-medium">Distinct</th>
                    <th className="px-3 py-1.5 text-left font-medium">Sample</th>
                    <th className="w-44 px-3 py-1.5 text-left font-medium">Role</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((c) => (
                    <tr key={c.name} className="border-t border-slate-100">
                      <td className="px-3 py-1 font-mono text-slate-800">{c.name}</td>
                      <td className="px-3 py-1 text-slate-500">{c.dtype}</td>
                      <td className="px-3 py-1 text-right tabular-nums text-slate-600">{num(c.n_unique)}</td>
                      <td className="max-w-[12rem] truncate px-3 py-1 font-mono text-slate-500" title={c.sample.join(", ")}>{c.sample.join(", ")}</td>
                      <td className="px-3 py-1">
                        <select
                          value={roleOf(c.name)}
                          onChange={(e) => setMapping({ ...mapping, roles: { ...mapping.roles, [c.name]: e.target.value as Role } })}
                          className="h-7 w-full rounded-md border border-slate-300 bg-white px-2 text-xs focus:outline-none focus:ring-2 focus:ring-sky-500"
                        >
                          {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
                            <option key={r} value={r}>{ROLE_LABEL[r]}</option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-6 py-4">
          <div className="min-w-0 flex-1 text-xs">
            {error ? (
              <span className="text-red-700">{error}</span>
            ) : problems.length ? (
              <span className="text-amber-700">{problems[0]}</span>
            ) : (
              <span className="text-slate-500">Ingesting replaces the active dataset for every user and retrains all models (~2–10 s).</span>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
            <Button onClick={onConfirm} disabled={busy || problems.length > 0}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />} Ingest & retrain models
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
