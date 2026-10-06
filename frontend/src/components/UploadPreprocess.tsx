"use client";
/** Universal preprocessor UI: pipeline steps, detected-type preview, ZIP/sheet report, telemetry mapping + result. */
import { useMemo } from "react";
import { AlertTriangle, CheckCircle2, Circle, FileArchive, Info, Loader2, RotateCcw, Settings2, UploadCloud, XCircle } from "lucide-react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartTooltip, axisProps } from "./common";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { INK, SERIES, cn, num } from "@/lib/utils";

export const ACCEPT = ".csv,.txt,.log,.json,.xlsx,.xls,.zip";
export const SUPPORTED_EXT = [".csv", ".txt", ".log", ".json", ".xlsx", ".xls", ".zip", ".xlsv"];
export const UNSUPPORTED_MSG = "Unsupported file type. Supported: CSV, TXT, LOG, JSON, XLS, XLSX, ZIP.";

export type TelemetryRole = "telemetry" | "context" | "log" | "ignore";
export interface StageInfo { key: string; label: string; status: "pending" | "done" | "warning" | "error" | "active"; detail: string }
export interface PIssue { severity: "error" | "warning" | "info"; message: string; column: string | null }
export interface PColumn {
  name: string; normalized: string; dtype: string; rows: number; missing: number; n_unique: number; invalid: number;
  sample: string[]; role: string; confidence: number; reason: string; alternatives: { role: string; confidence: number }[];
  ambiguous: boolean; unit: string | null; timestamp_parse_rate: number;
}
export interface Part {
  part_id: string; label: string; parser: string; data_type: string; data_type_label: string; rows: number; ok: boolean;
  columns: PColumn[]; issues: PIssue[]; parser_details?: Record<string, unknown>;
  execution: { available: boolean; reason: string | null; preview: any };
  telemetry: { available: boolean; reason: string | null; mapping: { timestamp: string | null; roles: Record<string, TelemetryRole> } };
}
export interface UniversalPreview {
  upload_id: string; filename: string; file_type: string; size_bytes: number; preprocessing_version: string;
  stages: StageInfo[]; parts: Part[]; default_part: string | null; file_issues?: PIssue[];
  archive: null | { files: { name: string; ext: string; size: number; status: string; reason: string; rows?: number }[]; combinable: boolean; combine_reason: string };
  sheets: { name: string; rows: number; columns: number; status: string }[];
}
export interface TelemetryMapping { timestamp: string | null; roles: Record<string, TelemetryRole> }

const STAGE_LABELS = ["File detected", "Parsing", "Detecting fields", "Mapping fields", "Validating", "Ready for ingestion"];
const ROLE_TEXT: Record<string, string> = {
  timestamp: "Timestamp", outcome: "Outcome (pass/fail)", performance: "Performance", telemetry: "Telemetry", config: "Config parameter",
  run_id: "Run ID", config_id: "Config ID", seed: "Seed", environment: "Environment", hardware: "Hardware", workload: "Workload",
  error: "Error signature", level: "Log level", log: "Log text", ignore: "Ignore", context: "Context",
};
const T_ROLES: TelemetryRole[] = ["telemetry", "context", "log", "ignore"];

/** Pipeline steps; while uploading (no stages yet) the first step spins. */
export function PreprocessSteps({ stages, busy, failed }: { stages?: StageInfo[]; busy?: boolean; failed?: string | null }) {
  const list: StageInfo[] = stages ?? STAGE_LABELS.map((label, i) => ({
    key: String(i), label, detail: "",
    status: failed ? (i === (/unsupported|extension|empty/i.test(failed) ? 0 : 1) ? "error" : i < (/unsupported|extension|empty/i.test(failed) ? 0 : 1) ? "done" : "pending") : busy && i === 0 ? "active" : "pending",
  }));
  return (
    <ol className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs" aria-label="Preprocessing steps">
      {list.map((s, i) => (
        <li key={s.key} className="flex items-center gap-1.5" title={s.detail || undefined}>
          {s.status === "done" ? <CheckCircle2 className="h-4 w-4 text-black" />
            : s.status === "warning" ? <AlertTriangle className="h-4 w-4 text-grey-600" />
            : s.status === "error" ? <XCircle className="h-4 w-4 text-red-600" />
            : s.status === "active" ? <Loader2 className="h-4 w-4 animate-spin text-black" />
            : <Circle className="h-4 w-4 text-grey-300" />}
          <span className={cn(s.status === "error" ? "font-semibold text-red-700" : s.status === "pending" ? "text-grey-500" : "text-black")}>{s.label}</span>
          {s.status === "warning" && <span className="text-2xs text-grey-500">(warnings)</span>}
          {i < list.length - 1 && <span className="ml-2 h-px w-6 bg-grey-200" />}
        </li>
      ))}
    </ol>
  );
}

function IssueList({ issues }: { issues: PIssue[] }) {
  if (!issues.length) return <div className="text-xs text-grey-500">No validation issues.</div>;
  const order = { error: 0, warning: 1, info: 2 };
  return (
    <ul className="space-y-1 text-xs">
      {[...issues].sort((a, b) => order[a.severity] - order[b.severity]).map((i, k) => (
        <li key={k} className={cn("flex items-start gap-2", i.severity === "error" ? "text-red-800" : i.severity === "warning" ? "text-black" : "text-grey-600")}>
          {i.severity === "error" ? <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-600" /> : i.severity === "warning" ? <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
          <span className="[overflow-wrap:anywhere]"><span className="sr-only">{i.severity}: </span>{i.message}</span>
        </li>
      ))}
    </ul>
  );
}

export function PreprocessPanel({
  pv, partId, setPartId, tmap, setTmap, busy, error, onExecution, onTelemetry, onCancel,
}: {
  pv: UniversalPreview; partId: string; setPartId: (id: string) => void; tmap: TelemetryMapping | null; setTmap: (m: TelemetryMapping) => void;
  busy: boolean; error: string | null; onExecution: () => void; onTelemetry: () => void; onCancel: () => void;
}) {
  const part = pv.parts.find((p) => p.part_id === partId) ?? pv.parts[0];
  const nTel = tmap ? Object.entries(tmap.roles).filter(([c, r]) => r === "telemetry" && c !== tmap.timestamp).length : 0;
  const tsCands = part.columns.filter((c) => c.role === "timestamp" || c.timestamp_parse_rate > 0.5 || /time|date/i.test(c.name));
  return (
    <Card>
      <CardHeader className="flex-wrap">
        <div className="min-w-0">
          <CardTitle>Preprocessing preview</CardTitle>
          <CardDescription>
            <span className="font-mono">{pv.filename}</span> · {pv.file_type.toUpperCase()} file · {(pv.size_bytes / 1024).toFixed(0)} KB · preprocessor v{pv.preprocessing_version}
          </CardDescription>
        </div>
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={busy}>Cancel</Button>
      </CardHeader>
      <CardContent className="space-y-5">
        <PreprocessSteps stages={pv.stages} />

        {pv.archive && (
          <section className="rounded-lg border border-grey-200">
            <div className="flex items-center gap-2 border-b border-grey-200 bg-grey-50 px-3 py-2 text-xs font-semibold text-black">
              <FileArchive className="h-4 w-4" /> Archive contents · {pv.archive.files.filter((f) => f.status === "supported").length} supported ·{" "}
              {pv.archive.files.filter((f) => f.status !== "supported").length} ignored or rejected
            </div>
            <div className="max-h-48 overflow-auto">
              <table className="w-full text-xs">
                <tbody>
                  {pv.archive.files.map((f) => (
                    <tr key={f.name} className="border-t border-grey-150 first:border-t-0">
                      <td className="px-3 py-1 font-mono text-grey-800 [overflow-wrap:anywhere]">{f.name}</td>
                      <td className="px-3 py-1 text-right tabular-nums text-grey-500">{(f.size / 1024).toFixed(1)} KB</td>
                      <td className={cn("px-3 py-1", f.status === "supported" ? "text-black" : f.status === "rejected" || f.status === "failed" ? "text-red-700" : "text-grey-500")}>
                        {f.status}{f.rows != null ? ` · ${num(f.rows)} rows` : ""}{f.reason ? ` · ${f.reason}` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="border-t border-grey-200 px-3 py-2 text-xs text-grey-600">
              {pv.archive.combinable ? "✓ " : ""}{pv.archive.combine_reason}
            </div>
          </section>
        )}

        {pv.sheets.length > 1 && (
          <div className="text-xs text-grey-600">
            Sheets: {pv.sheets.map((s) => `${s.name} (${s.status === "data" ? `${num(s.rows)} rows` : s.status})`).join(" · ")}
          </div>
        )}

        {pv.parts.length > 1 && (
          <label className="block max-w-md text-xs">
            <span className="mb-1 block font-medium text-grey-800">Dataset to ingest</span>
            <select value={part.part_id} onChange={(e) => setPartId(e.target.value)} disabled={busy}
              className="h-9 w-full rounded-[10px] border border-grey-300 bg-white px-2 text-sm hover:border-grey-400 focus:border-red-600 focus:shadow-focus focus:outline-none">
              {pv.parts.map((p) => <option key={p.part_id} value={p.part_id}>{p.label} · {num(p.rows)} rows · {p.data_type_label}</option>)}
            </select>
          </label>
        )}

        <div className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-lg border border-grey-200 px-4 py-3">
            <div className="eyebrow">Detected type</div>
            <div className="mt-1 font-semibold text-black">{part.data_type_label}</div>
            <div className="text-2xs text-grey-500">parser: {part.parser}</div>
          </div>
          <div className="rounded-lg border border-grey-200 px-4 py-3">
            <div className="eyebrow">Rows</div>
            <div className="mt-1 font-semibold tabular-nums text-black">{num(part.rows)}</div>
          </div>
          <div className="rounded-lg border border-grey-200 px-4 py-3">
            <div className="eyebrow">Columns</div>
            <div className="mt-1 font-semibold tabular-nums text-black">{part.columns.length}</div>
          </div>
        </div>

        <section>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-grey-500">Detected columns</div>
          <div className="overflow-x-auto rounded-lg border border-grey-200">
            <table className="w-full min-w-[560px] text-xs">
              <thead className="bg-grey-50 text-grey-500">
                <tr>
                  <th className="px-3 py-1.5 text-left font-medium">Column</th>
                  <th className="px-3 py-1.5 text-left font-medium">Type</th>
                  <th className="px-3 py-1.5 text-left font-medium">Detected role</th>
                  <th className="px-3 py-1.5 text-right font-medium">Confidence</th>
                  <th className="px-3 py-1.5 text-left font-medium">Unit</th>
                  <th className="px-3 py-1.5 text-right font-medium">Missing</th>
                  {tmap && <th className="w-36 px-3 py-1.5 text-left font-medium">Telemetry role</th>}
                </tr>
              </thead>
              <tbody>
                {part.columns.map((c) => (
                  <tr key={c.name} className="border-t border-grey-150" title={c.reason}>
                    <td className="px-3 py-1 font-mono text-grey-800 [overflow-wrap:anywhere]">{c.name}</td>
                    <td className="px-3 py-1 text-grey-500">{c.dtype}</td>
                    <td className="px-3 py-1 text-black">
                      {ROLE_TEXT[c.role] ?? c.role}
                      {c.ambiguous && <span className="ml-1 text-2xs text-red-700">ambiguous{c.alternatives[0] ? ` (or ${ROLE_TEXT[c.alternatives[0].role] ?? c.alternatives[0].role})` : ""}</span>}
                    </td>
                    <td className="px-3 py-1 text-right tabular-nums text-grey-600">{Math.round(c.confidence * 100)}%</td>
                    <td className="px-3 py-1 text-grey-600">{c.unit ?? (/Int|Float/.test(c.dtype) ? "unit unknown" : "–")}</td>
                    <td className="px-3 py-1 text-right tabular-nums text-grey-600">{c.missing ? num(c.missing) : "–"}</td>
                    {tmap && (
                      <td className="px-3 py-1">
                        {c.name === tmap.timestamp ? <span className="text-grey-600">timestamp</span> : (
                          <select value={tmap.roles[c.name] ?? "ignore"} disabled={busy} aria-label={`Telemetry role for ${c.name}`}
                            onChange={(e) => setTmap({ ...tmap, roles: { ...tmap.roles, [c.name]: e.target.value as TelemetryRole } })}
                            className="h-7 w-full rounded-md border border-grey-300 bg-white px-2 text-xs hover:border-grey-400 focus:border-red-600 focus:shadow-focus focus:outline-none">
                            {T_ROLES.map((r) => <option key={r} value={r}>{ROLE_TEXT[r]}</option>)}
                          </select>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-grey-500">Validation</div>
          <IssueList issues={[...(pv.file_issues ?? []), ...part.issues]} />
        </section>

        {part.telemetry.available && tmap && (
          <section className="rounded-lg border border-grey-200 bg-grey-50 p-4">
            <div className="eyebrow mb-2">Telemetry analysis</div>
            <label className="block max-w-sm text-xs">
              <span className="mb-1 block font-medium text-grey-800">Timestamp column</span>
              <select value={tmap.timestamp ?? ""} disabled={busy}
                onChange={(e) => {
                  const ts = e.target.value || null;
                  const roles = { ...tmap.roles };
                  if (tmap.timestamp && !(tmap.timestamp in roles)) roles[tmap.timestamp] = "context";
                  if (ts) delete roles[ts];
                  setTmap({ timestamp: ts, roles });
                }}
                className="h-9 w-full rounded-[10px] border border-grey-300 bg-white px-2 text-sm hover:border-grey-400 focus:border-red-600 focus:shadow-focus focus:outline-none">
                <option value="">— none (use row order) —</option>
                {(tsCands.length ? tsCands : part.columns).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </label>
            <p className="mt-2 text-xs text-grey-600">
              Measurements are stored as-is (units are taken from column names only; otherwise “unit unknown”). No PASS/FAIL labels are created.
              The active execution dataset and models are not changed.
            </p>
          </section>
        )}

        {error && (
          <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-grey-200 pt-4">
          {!part.execution.available && <span className="mr-auto text-xs text-grey-600">Execution-log ingestion: {part.execution.reason ?? "unavailable"}</span>}
          {part.telemetry.available && tmap && (
            <Button variant={part.execution.available ? "outline" : "primary"} onClick={onTelemetry} disabled={busy || nTel === 0}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />} Ingest as telemetry ({nTel} channel{nTel === 1 ? "" : "s"})
            </Button>
          )}
          {part.execution.available && (
            <Button variant="primary" onClick={onExecution} disabled={busy}>
              <Settings2 className="h-4 w-4" /> Map fields & ingest execution log
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

const fmt = (v: number | null | undefined) => (v == null || !isFinite(v) ? "–" : Math.abs(v) >= 1000 ? num(v, 0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(3));

export function TelemetryResult({ status, onClear, canClear, clearing }: { status: any; onClear: () => void; canClear: boolean; clearing: boolean }) {
  const { meta, summary } = status;
  const data = useMemo(() => summary.series.x.map((x: string | number, i: number) => {
    const row: Record<string, unknown> = { x };
    for (const [k, v] of Object.entries(summary.series.channels as Record<string, (number | null)[]>)) row[k] = v[i];
    return row;
  }), [summary]);
  const xFmt = (v: string | number) => (summary.series.x_kind === "time" ? String(v).slice(11, 19) : String(v));
  return (
    <Card>
      <CardHeader className="flex-wrap">
        <div className="min-w-0">
          <CardTitle>Telemetry dataset · {meta.part ?? meta.original_filename}</CardTitle>
          <CardDescription>
            {num(meta.rows)} samples · {meta.channels.length} channel{meta.channels.length === 1 ? "" : "s"}
            {summary.timing ? ` · ${summary.timing.start.slice(0, 19).replace("T", " ")} → ${summary.timing.end.slice(0, 19).replace("T", " ")} · median interval ${fmt(summary.timing.median_interval_s)} s · ${summary.timing.gaps} gap(s)` : " · no timestamp (row order)"}
          </CardDescription>
        </div>
        {canClear && (
          <Button variant="outline" size="sm" onClick={onClear} disabled={clearing}>
            {clearing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />} Remove telemetry dataset
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="overflow-x-auto rounded-lg border border-grey-200">
          <table className="w-full min-w-[640px] text-xs">
            <thead className="bg-grey-50 text-grey-500">
              <tr>
                {["Channel", "Unit", "Mean", "Min", "Max", "P95", "Missing", "Anomalies", "Trend / hour"].map((h, i) => (
                  <th key={h} className={cn("px-3 py-1.5 font-medium", i < 2 ? "text-left" : "text-right")}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {summary.channels.map((c: any) => (
                <tr key={c.name} className="border-t border-grey-150">
                  <td className="px-3 py-1 font-mono text-grey-800">{c.original}</td>
                  <td className="px-3 py-1 text-grey-600">{c.unit}</td>
                  {[c.mean, c.min, c.max, c.p95].map((v: number, i: number) => <td key={i} className="px-3 py-1 text-right tabular-nums text-black">{fmt(v)}</td>)}
                  <td className="px-3 py-1 text-right tabular-nums text-grey-600">{num(c.missing)}</td>
                  <td className={cn("px-3 py-1 text-right tabular-nums", c.anomalies ? "font-semibold text-red-700" : "text-grey-600")} title={c.anomaly_method}>{num(c.anomalies ?? 0)}</td>
                  <td className="px-3 py-1 text-right tabular-nums text-grey-600">{c.trend_per_hour == null ? "–" : `${c.trend_per_hour >= 0 ? "+" : ""}${fmt(c.trend_per_hour)}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          {meta.channels.map((ch: any, i: number) => (
            <figure key={ch.name} className="rounded-lg border border-grey-200 p-3">
              <figcaption className="mb-1 text-xs font-semibold text-black">{ch.original} <span className="font-normal text-grey-500">({ch.unit})</span></figcaption>
              <div className="h-40">
                <ResponsiveContainer>
                  <LineChart data={data} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
                    <CartesianGrid stroke={INK.grid} vertical={false} />
                    <XAxis dataKey="x" {...axisProps} tickFormatter={xFmt} minTickGap={40} />
                    <YAxis {...axisProps} width={48} domain={["auto", "auto"]} tickFormatter={(v: number) => fmt(v)} />
                    <Tooltip content={<ChartTooltip />} />
                    <Line isAnimationActive={false} dataKey={ch.name} name={ch.original} stroke={SERIES[i % 2]} strokeWidth={2} dot={false} connectNulls={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </figure>
          ))}
        </div>
        {summary.series.step > 1 && <div className="text-2xs text-grey-500">Charts show every {summary.series.step}th sample; statistics use all samples.</div>}

        {summary.correlations.length > 0 && (
          <div className="text-xs text-grey-600">
            <span className="font-semibold text-black">Strongest channel correlations:</span>{" "}
            {summary.correlations.slice(0, 3).map((c: any) => `${c.a} ↔ ${c.b} r=${c.r.toFixed(2)}`).join(" · ")} (n={summary.correlations[0].n})
          </div>
        )}

        <section className="rounded-lg border border-grey-200 bg-grey-50 p-4">
          <div className="eyebrow mb-2">Not available for this dataset</div>
          <ul className="grid gap-1 text-xs sm:grid-cols-2">
            {summary.unavailable.map((u: any) => (
              <li key={u.analysis} className="text-grey-600"><span className="font-medium text-black">{u.analysis}:</span> {u.message} <span className="text-grey-500">(needs {u.missing_field})</span></li>
            ))}
          </ul>
          {meta.warnings?.length > 0 && <div className="mt-3"><IssueList issues={meta.warnings.map((m: string) => ({ severity: "warning", message: m, column: null }))} /></div>}
        </section>
      </CardContent>
    </Card>
  );
}
