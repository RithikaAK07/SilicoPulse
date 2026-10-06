"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle, ArrowRight, CheckCircle2, Circle, Download, FileSpreadsheet, Loader2, Lock, RotateCcw, Search, Settings2, UploadCloud, X,
} from "lucide-react";
import { api, download, post, upload, type DatasetStatus } from "@/lib/api";
import {
  ACCEPT, PreprocessPanel, PreprocessSteps, SUPPORTED_EXT, TelemetryResult, UNSUPPORTED_MSG, assessOutcome, type Confirmation, type TelemetryMapping,
  type UniversalPreview,
} from "./UploadPreprocess";
import { useAuth } from "@/context/AuthContext";
import { SERIES, cn, num } from "@/lib/utils";
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
  confirmed?: string[];
}
interface Preview {
  upload_id: string;
  filename: string;
  size_bytes: number;
  rows: number;
  columns: ColumnInfo[];
  mapping: Mapping;
  low_card_values: Record<string, string[]>;
  confirmations?: Confirmation[];
}
type Stage = "idle" | "analyzing" | "preview" | "mapping" | "ingesting" | "done" | "error";

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
const ORDER: Stage[] = ["idle", "analyzing", "preview", "mapping", "ingesting", "done"];

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
  const [uni, setUni] = useState<UniversalPreview | null>(null);
  const [partId, setPartId] = useState<string>("main");
  const [tmap, setTmap] = useState<TelemetryMapping | null>(null);
  const [mode, setMode] = useState<"execution" | "telemetry">("execution");
  const [telemetry, setTelemetry] = useState<any>(null);
  const [telemetryIngested, setTelemetryIngested] = useState(false);
  const [clearing, setClearing] = useState(false);

  useEffect(() => {
    api<any>("/api/telemetry/status").then((t) => t?.active && setTelemetry(t)).catch(() => undefined);
  }, []);

  function selectPart(pv: UniversalPreview, id: string) {
    const part = pv.parts.find((p) => p.part_id === id) ?? pv.parts[0];
    setPartId(part.part_id);
    const tm = part.telemetry?.mapping;
    setTmap(tm ? { timestamp: tm.timestamp ?? null, roles: { ...(tm.roles ?? {}) } } : null);
    const ex = part.execution.preview;
    if (part.execution.available && ex) {
      setPreview({ upload_id: pv.upload_id, filename: part.label, size_bytes: pv.size_bytes, rows: ex.rows, columns: ex.columns, mapping: ex.mapping, low_card_values: ex.low_card_values, confirmations: part.execution.confirmations ?? [] });
      setMapping(ex.mapping);
    } else {
      setPreview(null);
      setMapping(null);
    }
  }

  async function analyze(f: File) {
    setFile(f);
    setUni(null);
    setPreview(null);
    setResult(null);
    setMode("execution");
    setTelemetryIngested(false);
    if (!SUPPORTED_EXT.some((x) => f.name.toLowerCase().endsWith(x))) {
      setError(UNSUPPORTED_MSG);
      setStage("error");
      return;
    }
    setError(null);
    setStage("analyzing");
    try {
      const form = new FormData();
      form.append("file", f);
      const pv = await upload<UniversalPreview>("/api/upload/preview", form);
      setUni(pv);
      selectPart(pv, pv.default_part ?? pv.parts[0]?.part_id ?? "main");
      setStage("preview");
    } catch (e) {
      setError((e as Error).message);
      setStage("error");
    }
  }

  async function ingest() {
    if (!preview || !mapping) return;
    setStage("ingesting");
    setMode("execution");
    setError(null);
    try {
      const form = new FormData();
      form.append("upload_id", preview.upload_id);
      form.append("part_id", partId);
      form.append("mode", "execution");
      form.append("mapping", JSON.stringify(mapping));
      const res = await upload("/api/upload/ingest", form);
      setResult(res);
      setStage("done");
      await refreshDataset();
    } catch (e) {
      setError((e as Error).message);
      setStage("mapping");
    }
  }

  async function ingestTelemetry() {
    if (!uni || !tmap) return;
    setStage("ingesting");
    setMode("telemetry");
    setError(null);
    try {
      const form = new FormData();
      form.append("upload_id", uni.upload_id);
      form.append("part_id", partId);
      form.append("mode", "telemetry");
      form.append("mapping", JSON.stringify(tmap));
      const res = await upload<any>("/api/upload/ingest", form);
      setTelemetry(res.telemetry);
      setTelemetryIngested(true);
      setResult(null);
      setUni(null);
      setStage("done");
    } catch (e) {
      setError((e as Error).message);
      setStage("preview");
    }
  }

  async function clearTelemetry() {
    setClearing(true);
    try {
      await post("/api/telemetry/reset", {});
      setTelemetry(null);
      setTelemetryIngested(false);
    } finally {
      setClearing(false);
    }
  }

  function cancel() {
    setStage("idle");
    setUni(null);
    setPreview(null);
    setFile(null);
    setError(null);
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
    <div className="grid gap-6">
      <Card>
        <CardHeader className="flex-wrap">
          <div>
            <CardTitle>Bring your own execution logs</CardTitle>
            <CardDescription>
              Upload CSV, TXT, LOG, JSON, Excel (XLS/XLSX) or a ZIP of them. Files are parsed and columns auto-classified into outcome, performance, configuration parameters, randomized variables and telemetry; you can adjust the mapping before ingesting. Files without a pass/fail outcome (e.g. machine health telemetry) are ingested as telemetry, never with invented labels.
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="lg" onClick={sample} disabled={downloading}>
              {downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <span aria-hidden>📥</span>} Download Sample SanDisk Execution CSV
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-grey-200 bg-grey-50 px-4 py-3 text-sm">
            {dataset?.source === "uploaded" ? <FileSpreadsheet className="h-5 w-5 text-black" /> : <FileSpreadsheet className="h-5 w-5 text-grey-500" />}
            <div className="flex-1">
              <div className="font-medium text-black">
                {dataset?.label ?? "…"}
                {dataset?.source === "uploaded" && dataset.filename ? ` · ${dataset.filename}` : ""}
              </div>
              <div className="text-xs text-grey-500">
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
            <div className="flex items-start gap-3 rounded-xl border border-grey-200 bg-grey-50 p-5 text-sm text-black">
              <Lock className="mt-0.5 h-5 w-5 shrink-0 text-red-600" strokeWidth={1.75} />
              <div>
                <div className="font-semibold">Read-only role</div>
                Executive Viewers can explore every dashboard and download the sample CSV, but uploading data requires a Validation Lead or VLSI Engineer account.
              </div>
            </div>
          ) : (
            <>
              <div
                role="button"
                tabIndex={0}
                aria-label="Upload data file"
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
                  "group flex cursor-pointer flex-col items-center justify-center rounded-xl border-[1.5px] border-dashed px-6 py-12 text-center",
                  drag ? "border-red-600 bg-red-50" : "border-grey-300 bg-white hover:border-red-600 hover:bg-red-50",
                  busy && "cursor-wait opacity-70",
                )}
              >
                <UploadCloud className={cn("h-10 w-10", drag ? "text-red-600" : "text-grey-600 group-hover:text-red-600")} strokeWidth={1.5} />
                <div className="mt-3 text-base font-semibold text-black">{drag ? "Drop to analyze" : "Drag & drop a file here, or click to browse"}</div>
                <div className="mt-1 text-xs text-grey-600">CSV · TXT · LOG · JSON · XLSX · XLS · ZIP · up to 200 MB · execution logs need a pass/fail column and at least {50} rows</div>
                <input
                  ref={inputRef}
                  type="file"
                  accept={ACCEPT}
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) analyze(f);
                    e.target.value = "";
                  }}
                />
              </div>

              {(file || stage === "error") && (
                <div className="mt-6 rounded-xl border border-grey-200 p-4">
                  {file && (
                    <div className="mb-3 flex flex-wrap items-center gap-3">
                      <FileSpreadsheet className="h-5 w-5 text-black" />
                      <span className="font-mono text-sm text-grey-800">{file.name}</span>
                      <span className="text-xs text-grey-500">{(file.size / 1024).toFixed(0)} KB</span>
                      {uni && <span className="text-xs text-grey-500">· detected {uni.file_type.toUpperCase()}</span>}
                      {preview && <span className="text-xs text-grey-500">· {num(preview.rows)} rows · {preview.columns.length} columns</span>}
                      {stage === "mapping" && (
                        <Button size="sm" variant="outline" className="ml-auto" onClick={() => setStage("mapping")}>
                          <Settings2 className="h-4 w-4" /> Edit mapping
                        </Button>
                      )}
                    </div>
                  )}
                  {(stage === "analyzing" || (stage === "error" && !uni)) && (
                    <div className="mb-3"><PreprocessSteps busy={stage === "analyzing"} failed={stage === "error" ? error : null} /></div>
                  )}
                  {mode === "execution" && <StepIndicator stage={stage} />}
                  {mode === "telemetry" && stage === "done" && telemetryIngested && telemetry?.active && (
                    <div className="flex flex-wrap items-center gap-2 text-sm text-black" role="status">
                      <CheckCircle2 className="h-4 w-4 text-black" />
                      <span className="font-semibold">Telemetry dataset ingested successfully.</span>
                      <span className="text-xs text-grey-500">
                        {num(telemetry.meta.rows)} samples · {telemetry.meta.channels.length} channel{telemetry.meta.channels.length === 1 ? "" : "s"} · no PASS/FAIL labels created · execution dataset unchanged
                      </span>
                    </div>
                  )}
                  {error && stage !== "preview" && (
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

      {uni && canUpload && (stage === "preview" || (stage === "ingesting" && mode === "telemetry")) && (
        <PreprocessPanel
          pv={uni}
          partId={partId}
          setPartId={(id) => selectPart(uni, id)}
          tmap={tmap}
          setTmap={setTmap}
          busy={stage === "ingesting"}
          error={error}
          onExecution={() => {
            setError(null);
            setStage("mapping");
          }}
          onTelemetry={ingestTelemetry}
          onCancel={cancel}
        />
      )}

      {telemetry?.active && (
        <TelemetryResult status={telemetry} onClear={clearTelemetry} canClear={canUpload} clearing={clearing} justIngested={telemetryIngested && stage === "done"} />
      )}

      {preview && mapping && (stage === "mapping" || stage === "ingesting") && (
        <MappingDialog
          preview={preview}
          mapping={mapping}
          setMapping={setMapping}
          busy={stage === "ingesting"}
          error={error}
          onCancel={() => {
            setError(null);
            setStage(uni ? "preview" : "idle");
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
              <CheckCircle2 className="h-4 w-4 text-black" />
            ) : active ? (
              stage === "mapping" ? <Settings2 className="h-4 w-4 text-black" /> : <Loader2 className="h-4 w-4 animate-spin text-black" />
            ) : (
              <Circle className="h-4 w-4 text-grey-300" />
            )}
            <span className={cn(done ? "text-black" : active ? "font-semibold text-red-700" : "text-grey-500")}>{s.label}</span>
            {i < STEPS.length - 1 && <span className="ml-2 h-px w-6 bg-grey-200" />}
          </li>
        );
      })}
    </ol>
  );
}

function SuccessCard({ result }: { result: any }) {
  const s = result.summary;
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-4 pt-5">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full border border-grey-300 bg-white"><CheckCircle2 className="h-6 w-6 text-black" strokeWidth={1.75} /></span>
        <div className="flex-1">
          <div className="font-semibold text-black">Dataset ingested & models retrained</div>
          <div className="text-sm text-grey-600">
            {num(s.rows)} executions · {s.config_params} config parameters · {s.random_vars} randomized variables · {s.profiles} configuration profiles ·{" "}
            {num(s.failures)} failures. Risk model AUC <b>{result.model_auc.toFixed(3)}</b>, trained in {result.training_seconds}s.
          </div>
          {result.dataset.leakage_dropped?.length > 0 && (
            <div className="mt-1 text-xs text-red-900">
              Dropped {result.dataset.leakage_dropped.join(", ")}: it perfectly predicts pass/fail (a leaked label), so training on it would make every analysis meaningless.
            </div>
          )}
          {result.dataset.synthetic_columns.length > 0 && (
            <div className="mt-1 text-xs text-grey-500">Not present in the uploaded file (neutral defaults used): {result.dataset.synthetic_columns.join(", ")}</div>
          )}
          <UnavailableAnalyses synthetic={result.dataset.synthetic_columns} mapping={result.mapping} />
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

/** Analyses that need a field the uploaded file does not contain: explained, never fabricated. */
const ANALYSIS_NEEDS: { analysis: string; field: string; missing: (syn: string[], m: any) => boolean }[] = [
  { analysis: "Performance / throughput analysis & Pareto trade-offs", field: "performance metric", missing: (s) => s.includes("throughput_mbps") },
  { analysis: "Seed repeatability & determinism (Q4)", field: "random seed", missing: (s) => s.includes("seed") },
  { analysis: "Trend & drift over real time", field: "timestamp", missing: (s) => s.includes("timestamp") },
  { analysis: "Root-cause fingerprints by error signature (Q5)", field: "error signature", missing: (_s, m) => !m?.error_signature },
  { analysis: "Breakdown by environment / hardware / workload", field: "environment, hardware or workload", missing: (s) => ["environment", "hardware", "workload"].every((c) => s.includes(c)) },
  { analysis: "Telemetry correlations (IOPS, latency, CPU, memory, retries)", field: "telemetry columns",
    missing: (s) => ["iops", "latency_p99_ms", "cpu_util", "mem_util", "retry_count", "instability_index"].every((c) => s.includes(c)) },
];
function UnavailableAnalyses({ synthetic, mapping }: { synthetic: string[]; mapping: any }) {
  const rows = ANALYSIS_NEEDS.filter((a) => a.missing(synthetic ?? [], mapping));
  if (!rows.length) return null;
  return (
    <ul className="mt-2 space-y-0.5 text-xs text-grey-600" aria-label="Analyses not available">
      {rows.map((a) => (
        <li key={a.analysis}>
          <span className="font-medium text-black">{a.analysis}:</span> Required field not available for this analysis ({a.field}).
        </li>
      ))}
    </ul>
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
      next.fail_values = v ? assessOutcome(v, vals).fail_values : [];
      next.confirmed = (mapping.confirmed ?? []).filter((f) => f !== "outcome");
    }
    if (key === "timestamp") next.confirmed = (mapping.confirmed ?? []).filter((f) => f !== "timestamp");
    setMapping(next);
  };
  const problems: string[] = [];
  if (!mapping.outcome) problems.push("Choose the outcome (pass/fail) column.");
  else if (!outcomeValues) problems.push("The outcome column must have 12 or fewer distinct values.");
  else if (!mapping.fail_values.length) problems.push("Tick at least one value that means FAIL.");
  if (counts.config === 0) problems.push("Mark at least one column as a configuration parameter.");
  // mappings that could not be determined reliably must be confirmed by the user (never guessed)
  const needs: { field: "outcome" | "timestamp"; message: string }[] = [];
  const oa = mapping.outcome && outcomeValues ? assessOutcome(mapping.outcome, outcomeValues) : null;
  if (oa && !oa.reliable) {
    const fromServer = preview.confirmations?.find((c) => c.field === "outcome" && c.column === mapping.outcome);
    needs.push({ field: "outcome", message: fromServer?.message ?? oa.reason });
  }
  const tsc = preview.confirmations?.find((c) => c.field === "timestamp" && c.column === mapping.timestamp);
  if (tsc) needs.push({ field: "timestamp", message: tsc.message });
  const isConfirmed = (f: string) => (mapping.confirmed ?? []).includes(f);
  needs.filter((n) => !isConfirmed(n.field)).forEach((n) => problems.push(n.field === "outcome" ? "Confirm which value(s) mean FAIL." : "Confirm the date order of the timestamp."));
  const toggleConfirm = (f: string) =>
    setMapping({ ...mapping, confirmed: isConfirmed(f) ? (mapping.confirmed ?? []).filter((x) => x !== f) : [...(mapping.confirmed ?? []), f] });

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 backdrop-blur-sm sm:items-center" role="dialog" aria-modal="true" aria-label="Column mapping">
      <div className="flex max-h-[92vh] w-full max-w-4xl flex-col rounded-2xl border border-grey-200 bg-white shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-grey-200 px-6 py-4">
          <div>
            <h2 className="text-base font-semibold text-black">Map columns</h2>
            <p className="text-xs text-grey-500">
              <span className="font-mono">{preview.filename}</span> · {num(preview.rows)} rows · {cols.length} columns. Suggestions are auto-detected; adjust anything that looks wrong.
            </p>
          </div>
          <button onClick={onCancel} className="rounded-md p-1 text-grey-500 hover:bg-grey-100" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {needs.length > 0 && (
            <section className="space-y-2" aria-label="Confirmation needed">
              {needs.map((n) => (
                <label key={n.field} className="flex cursor-pointer items-start gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-black">
                  <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-red-600" checked={isConfirmed(n.field)} onChange={() => toggleConfirm(n.field)} />
                  <span>
                    <span className="font-semibold">Confirmation needed · {n.field === "outcome" ? "PASS/FAIL values" : "date order"}.</span> {n.message}{" "}
                    <span className="text-grey-600">
                      {n.field === "outcome" ? "Tick the FAIL value(s) below, then check this box to confirm." : "Check this box to confirm, or choose another timestamp column."}
                    </span>
                  </span>
                </label>
              ))}
            </section>
          )}
          <section className="grid gap-6 md:grid-cols-2">
            <div className="rounded-xl border border-red-200 bg-red-50 p-4">
              <div className="eyebrow mb-2">Outcome (pass / fail) · required</div>
              <Select value={mapping.outcome ?? ""} placeholder="Select column…" options={colNames} onValueChange={(v) => setField("outcome", v)} />
              {outcomeValues && (
                <div className="mt-3">
                  <div className="mb-1.5 text-xs text-grey-600">Values that mean <b>FAIL</b>:</div>
                  <div className="flex flex-wrap gap-1.5">
                    {outcomeValues.map((v) => {
                      const on = mapping.fail_values.includes(v);
                      return (
                        <button
                          key={v}
                          onClick={() => setMapping({ ...mapping, fail_values: on ? mapping.fail_values.filter((x) => x !== v) : [...mapping.fail_values, v] })}
                          className={cn(
                            "rounded-md border px-2.5 py-1 font-mono text-xs transition-colors",
                            on ? "border-red-600 bg-red-50 text-red-900" : "border-grey-300 bg-white text-grey-600 hover:border-grey-400",
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
            <div className="rounded-xl border border-grey-300 bg-grey-50 p-4">
              <div className="mb-2 text-2xs font-semibold uppercase tracking-[0.08em] text-grey-600">Performance metric · recommended</div>
              <Select value={mapping.performance ?? NONE} options={[NONE, ...numeric]} onValueChange={(v) => setField("performance", v)} />
              <p className="mt-2 text-xs text-grey-600">Numeric metric to maximize (throughput, bandwidth, IOPS, score). Drives Pareto analysis and recommendations.</p>
            </div>
          </section>

          <section>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-grey-500">Optional fields</div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {OPTIONAL_FIELDS.map((f) => (
                <label key={f.key} className="block">
                  <span className="mb-1 flex items-baseline justify-between gap-2 text-xs">
                    <span className="font-medium text-grey-800">{f.label}</span>
                    <span className="truncate text-2xs text-grey-500">{f.hint}</span>
                  </span>
                  <Select value={mapping[f.key] ?? NONE} options={[NONE, ...(f.key === "seed" ? numeric : colNames)]} onValueChange={(v) => setField(f.key, v)} />
                </label>
              ))}
            </div>
          </section>

          <section>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-grey-500">Remaining columns · role</div>
              <div className="flex flex-wrap gap-1.5">
                <Badge color={SERIES[0]}>{counts.config} config</Badge>
                <Badge color={SERIES[1]}>{counts.random} randomized</Badge>
                <Badge color={SERIES[2]}>{counts.telemetry} telemetry</Badge>
                <Badge color={SERIES[3]}>{counts.ignore} ignored</Badge>
              </div>
            </div>
            <div className="relative mb-2">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-grey-500" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter columns…"
                className="h-10 w-full rounded-[10px] border border-grey-300 pl-8 pr-3 text-sm hover:border-grey-400 focus:border-red-600 focus:shadow-focus focus:outline-none"
              />
            </div>
            <div className="max-h-72 overflow-auto rounded-lg border border-grey-200">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-grey-50 text-grey-500">
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
                    <tr key={c.name} className="border-t border-grey-150">
                      <td className="px-3 py-1 font-mono text-grey-800">{c.name}</td>
                      <td className="px-3 py-1 text-grey-500">{c.dtype}</td>
                      <td className="px-3 py-1 text-right tabular-nums text-grey-600">{num(c.n_unique)}</td>
                      <td className="max-w-[12rem] truncate px-3 py-1 font-mono text-grey-500" title={c.sample.join(", ")}>{c.sample.join(", ")}</td>
                      <td className="px-3 py-1">
                        <select
                          value={roleOf(c.name)}
                          onChange={(e) => setMapping({ ...mapping, roles: { ...mapping.roles, [c.name]: e.target.value as Role } })}
                          className="h-7 w-full rounded-md border border-grey-300 bg-white px-2 text-xs hover:border-grey-400 focus:border-red-600 focus:shadow-focus focus:outline-none"
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

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-grey-200 px-6 py-4">
          <div className="min-w-0 flex-1 text-xs">
            {error ? (
              <span className="text-red-700">{error}</span>
            ) : problems.length ? (
              <span className="text-red-900">{problems[0]}</span>
            ) : (
              <span className="text-grey-500">Ingesting replaces the active dataset for every user and retrains all models (~2–10 s).</span>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
            <Button variant="primary" onClick={onConfirm} disabled={busy || problems.length > 0}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />} Ingest & retrain models
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
