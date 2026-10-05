"use client";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Fingerprint, GitCompare, Shuffle, Terminal } from "lucide-react";
import { api, useRootCause } from "@/lib/api";
import { STATUS, cn, num, pct, riskStatus, DIVERGING } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { ErrorState, Loading, PageHeader, Spinner } from "@/components/common";

export default function RootCausePage() {
  const { data, error, isLoading } = useRootCause();
  if (error) return <ErrorState error={error} />;
  return (
    <>
      <PageHeader
        badge="Q5 · Q6 — Root Cause & Change Impact"
        title="Root Cause & Log Intelligence"
        subtitle="Failure fingerprints mined from config conditions and log anomalies, plus a side-by-side diff engine for any passing vs failing execution."
      />
      {isLoading || !data ? (
        <Loading />
      ) : (
        <div className="grid gap-6">
          <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
            {data.fingerprints.map((f: any) => (
              <FingerprintCard key={f.signature} f={f} />
            ))}
          </div>
          <LogAnomalies rows={data.log_anomalies} />
          <DiffEngine signatures={data.fingerprints.map((f: any) => f.signature)} />
        </div>
      )}
    </>
  );
}

function FingerprintCard({ f }: { f: any }) {
  const det = f.deterministic_share > 0.5;
  return (
    <Card className="flex flex-col">
      <CardHeader className="pb-1">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 font-mono text-[13px]">
            <Fingerprint className="h-5 w-5 shrink-0 text-red-600" strokeWidth={1.75} />
            <span className="truncate">{f.signature}</span>
          </CardTitle>
          <CardDescription>{f.description}</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3 text-xs">
        <div className="flex flex-wrap gap-1.5">
          <Badge className="border-red-100 bg-red-100 text-red-900">{num(f.count)} failures · {pct(f.share)}</Badge>
          <Badge className={det ? "border-transparent bg-grad-black text-white" : "border-grey-500 bg-transparent text-black"}>{det ? `Deterministic ${pct(f.deterministic_share, 0)}` : `Stochastic ${pct(1 - f.deterministic_share, 0)}`}</Badge>
          <Badge className="border-grey-200 bg-grey-50 font-mono font-medium text-grey-800">{f.fingerprint}</Badge>
          {f.tendency && (
            <Badge className={cn("capitalize", f.tendency === "deterministic" ? "border-transparent bg-grad-black text-white" : f.tendency === "stochastic" ? "border-grey-500 bg-transparent text-black" : "")}>
              tendency: {f.tendency}
            </Badge>
          )}
        </div>
        <div>
          <div className="mb-1 text-2xs uppercase tracking-wide text-grey-500">Precursor conditions (lift)</div>
          {f.top_conditions.length ? (
            f.top_conditions.map((c: any) => (
              <div key={c.condition} className="flex items-center justify-between gap-2 py-0.5">
                <code className="truncate text-black">{c.condition}</code>
                <span className="shrink-0 tabular-nums text-grey-800">
                  {c.lift}× <span className="text-grey-500">· {pct(c.support, 0)}</span>
                </span>
              </div>
            ))
          ) : (
            <span className="text-grey-500">No single dominant condition (diffuse cause)</span>
          )}
        </div>
        {(f.associated_seeds?.length > 0 || f.associated_environment?.length > 0) && (
          <div>
            <div className="mb-1 text-2xs uppercase tracking-wide text-grey-500">Associated environment & randomization</div>
            {f.associated_environment?.map((e: any) => (
              <div key={e.variable} className="flex justify-between gap-2 py-0.5">
                <code className="text-black">{e.variable}</code>
                <span className="tabular-nums text-grey-800">{e.mean_in_signature} vs {e.mean_in_passing_runs} <span className="text-grey-500">· d={e.cohens_d}</span></span>
              </div>
            ))}
            {f.associated_seeds?.map((sd: any) => (
              <div key={sd.seed} className="flex justify-between gap-2 py-0.5">
                <code className="text-black">seed {sd.seed}</code>
                <span className="tabular-nums text-grey-800">{sd.lift}× <span className="text-grey-500">· z={sd.z_score}</span></span>
              </div>
            ))}
          </div>
        )}
        <div className="mt-auto">
          <div className="mb-1 text-2xs uppercase tracking-wide text-grey-500">Log signature</div>
          <div className="space-y-0.5 rounded-md bg-grey-50 p-2 font-mono text-2xs leading-relaxed">
            {f.log_templates.map((t: string) => (
              <div key={t} className={t.startsWith("FATAL") ? "font-semibold text-black" : t.startsWith("ERROR") ? "text-red-700" : "text-grey-600"}>
                {t}
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function LogAnomalies({ rows }: { rows: any[] }) {
  const max = Math.max(...rows.map((r) => r.count));
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Terminal className="h-4 w-4 text-black" /> Log anomaly clusters
          </CardTitle>
          <CardDescription>WARN/ERROR/FATAL lines templated (numbers and hex IDs → &lt;*&gt;) and clustered. Specificity = share belonging to the primary signature.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-grey-500">
              <tr>
                <th className="py-1.5 text-left font-medium">Severity</th>
                <th className="py-1.5 text-left font-medium">Template</th>
                <th className="py-1.5 text-left font-medium">Primary signature</th>
                <th className="py-1.5 text-right font-medium">Specificity</th>
                <th className="w-40 py-1.5 text-right font-medium">Occurrences</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.template} className="border-t border-grey-200">
                  <td className="py-1.5">
                    <Badge
                      className={r.severity === "FATAL" ? "border-transparent bg-grad-black text-white" : "border-grey-200 bg-white text-black"}
                      color={r.severity === "WARN" ? "#71717A" : "#DC2626"}
                    >
                      {r.severity}
                    </Badge>
                  </td>
                  <td className="max-w-md truncate py-1.5 font-mono text-2xs text-grey-800" title={r.template}>{r.template}</td>
                  <td className="py-1.5 font-mono text-2xs text-grey-500">{r.primary_signature}</td>
                  <td className="py-1.5 text-right tabular-nums">{pct(r.specificity, 0)}</td>
                  <td className="py-1.5">
                    <div className="flex items-center justify-end gap-2">
                      <div className="h-1.5 w-20 rounded bg-grey-100">
                        <div className="h-1.5 rounded bg-black" style={{ width: `${(r.count / max) * 100}%` }} />
                      </div>
                      <span className="w-10 text-right tabular-nums">{num(r.count)}</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function DiffEngine({ signatures }: { signatures: string[] }) {
  const [sig, setSig] = useState("");
  const [runA, setRunA] = useState("");
  const [runB, setRunB] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(true);
  const passRuns = useQuery({ queryKey: ["runs", "pass"], queryFn: () => api<any[]>("/api/runs?outcome=pass&limit=80") });
  const failRuns = useQuery({ queryKey: ["runs", "fail", sig], queryFn: () => api<any[]>(`/api/runs?outcome=fail&limit=80${sig ? `&signature=${sig}` : ""}`) });
  const diff = useQuery({ queryKey: ["diff", runA, runB], queryFn: () => api(`/api/diff?run_a=${runA}&run_b=${runB}`), enabled: !!runA && !!runB });

  const autoPair = async () => {
    const p = await api<{ run_a: string; run_b: string }>(`/api/diff/suggest${sig ? `?signature=${sig}` : ""}`);
    setRunA(p.run_a);
    setRunB(p.run_b);
  };
  useEffect(() => {
    autoPair();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const d: any = diff.data;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <GitCompare className="h-4 w-4 text-black" /> Q6 · Execution diff & change impact
          </CardTitle>
          <CardDescription>Select Run A (pass) and Run B (fail). Changed keys are ranked by model importance; SHAP Δ shows which change moved the risk.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 md:grid-cols-[1fr_1fr_1fr_auto] md:items-end">
          <Field label="Failure signature filter">
            <Select value={sig} placeholder="Any signature" options={signatures} onValueChange={setSig} />
          </Field>
          <Field label="Run A (pass)">
            <RunPicker value={runA} onChange={setRunA} runs={passRuns.data ?? []} />
          </Field>
          <Field label="Run B (fail)">
            <RunPicker value={runB} onChange={setRunB} runs={failRuns.data ?? []} />
          </Field>
          <Button variant="primary" onClick={autoPair}>
            <Shuffle className="h-4 w-4" /> Auto-pair nearest
          </Button>
        </div>

        {diff.isFetching && <div className="mt-4"><Spinner label="Diffing executions…" /></div>}
        {diff.error && <p className="mt-4 text-sm text-red-700">{String(diff.error)}</p>}
        {d && !diff.isFetching && (
          <div className="mt-5 grid gap-6">
            <div className="grid gap-3 md:grid-cols-[1fr_auto_1fr] md:items-center">
              <RunHeader r={d.run_a} risk={d.predicted_risk.a} />
              <ArrowRight className="mx-auto hidden h-5 w-5 text-grey-500 md:block" />
              <RunHeader r={d.run_b} risk={d.predicted_risk.b} />
            </div>
            <div className="text-xs text-grey-500">
              {d.summary.config_changes} config parameters and {d.summary.random_changes} randomized variables differ.{" "}
              {d.run_a.config_id === d.run_b.config_id && <b className="font-semibold text-black">Same configuration profile — the outcome flipped through execution context and randomized variables, not configuration (stochastic failure).</b>}
            </div>

            <div className="grid gap-6 xl:grid-cols-2">
              <div>
                <SectionTitle>Change impact (SHAP Δ on failure log-odds)</SectionTitle>
                {d.change_impact.length ? (
                  <div className="space-y-1">
                    {d.change_impact.map((c: any) => {
                      const w = Math.min(100, (Math.abs(c.shap_delta) / Math.max(...d.change_impact.map((x: any) => Math.abs(x.shap_delta)), 0.01)) * 100);
                      return (
                        <div key={c.feature} className="grid grid-cols-[10rem_1fr_3.5rem] items-center gap-2 text-xs">
                          <span className="truncate font-mono text-grey-800">{c.feature}</span>
                          <div className="h-3 rounded bg-grey-100">
                            <div className="h-3 rounded" style={{ width: `${w}%`, background: c.shap_delta > 0 ? DIVERGING.raises : DIVERGING.lowers }} />
                          </div>
                          <span className="text-right tabular-nums">{c.shap_delta > 0 ? "+" : ""}{c.shap_delta.toFixed(2)}</span>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-xs text-grey-500">No pre-execution parameters differ — see randomized variables.</p>
                )}
              </div>
              <div>
                <SectionTitle>Telemetry delta</SectionTitle>
                <DiffTable rows={d.telemetry} onlyChanged={false} />
              </div>
            </div>

            <div className="grid gap-6 xl:grid-cols-2">
              <div>
                <div className="flex items-center justify-between">
                  <SectionTitle>Configuration diff</SectionTitle>
                  <label className="flex items-center gap-1.5 text-2xs text-grey-500">
                    <input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} /> changed only
                  </label>
                </div>
                <DiffTable rows={[...d.context, ...d.config]} onlyChanged={onlyChanged} />
              </div>
              <div>
                <SectionTitle>Randomized variables diff</SectionTitle>
                <DiffTable rows={d.random.slice(0, 14)} onlyChanged={false} />
              </div>
            </div>

            <div>
              <SectionTitle>Log diff (aligned on templates)</SectionTitle>
              <div className="grid overflow-hidden rounded-lg border border-grey-200 font-mono text-2xs md:grid-cols-2">
                <div className="border-b border-grey-200 bg-white px-3 py-1.5 text-grey-500 md:border-b-0 md:border-r">{d.run_a.run_id}</div>
                <div className="bg-white px-3 py-1.5 text-grey-500">{d.run_b.run_id}</div>
                {d.logs.map((l: any, i: number) => (
                  <LogRow key={i} l={l} />
                ))}
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function LogRow({ l }: { l: any }) {
  const cls = (side: "a" | "b") =>
    cn(
      "whitespace-pre-wrap break-all px-3 py-0.5",
      l.op === "equal" && "text-grey-500",
      l.op !== "equal" && side === "a" && l.a && "bg-grey-100 text-black",
      l.op !== "equal" && side === "b" && l.b && (l.anomalous ? "bg-red-50 font-semibold text-red-900" : "bg-red-50 text-red-900"),
    );
  return (
    <>
      <div className={cn(cls("a"), "md:border-r md:border-grey-200")}>{l.a ? (l.op !== "equal" ? "− " : "  ") + l.a : ""}</div>
      <div className={cls("b")}>{l.b ? (l.op !== "equal" ? "+ " : "  ") + l.b : ""}</div>
    </>
  );
}

function DiffTable({ rows, onlyChanged }: { rows: any[]; onlyChanged: boolean }) {
  const shown = onlyChanged ? rows.filter((r) => r.changed) : rows;
  return (
    <div className="max-h-72 overflow-auto rounded-lg border border-grey-200">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-white text-grey-500">
          <tr>
            <th className="px-3 py-1.5 text-left font-medium">Key</th>
            <th className="px-3 py-1.5 text-right font-medium">Run A</th>
            <th className="px-3 py-1.5 text-right font-medium">Run B</th>
            <th className="px-3 py-1.5 text-right font-medium">Δ</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {shown.map((r) => (
            <tr key={r.key} className={cn("border-t border-grey-200", r.changed && "bg-grey-50")}>
              <td className="px-3 py-1 font-mono text-grey-800">{r.key}</td>
              <td className={cn("px-3 py-1 text-right", r.changed ? "text-black" : "text-grey-600")}>{fmt(r.a)}</td>
              <td className={cn("px-3 py-1 text-right", r.changed ? "font-semibold text-red-600" : "text-grey-600")}>{fmt(r.b)}</td>
              <td className={cn("px-3 py-1 text-right", r.pct > 0 ? "font-semibold text-red-600" : r.pct < 0 ? "text-black" : "text-grey-600")}>
                {r.pct != null ? `${r.pct > 0 ? "+" : ""}${r.pct}%` : r.changed ? "≠" : ""}
              </td>
            </tr>
          ))}
          {!shown.length && (
            <tr>
              <td colSpan={4} className="px-3 py-3 text-center text-grey-500">Identical</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

const fmt = (v: unknown) => (typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(3)) : String(v));

function RunHeader({ r, risk }: { r: any; risk: number }) {
  const pass = r.outcome === "pass";
  const st = riskStatus(risk);
  return (
    <div className={cn("rounded-xl border p-4 shadow-card", pass ? "border-grey-300 bg-white" : "border-red-600 bg-grad-fail")}>
      <div className="flex items-center justify-between">
        <span className="font-mono text-sm text-black">{r.run_id}</span>
        <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-2xs font-semibold text-white", pass ? "bg-grad-black" : "bg-red-600")}>
          {pass ? "✓ PASS" : "✕ FAIL"}
        </span>
      </div>
      <div className="mt-1 text-xs text-grey-600">
        {r.config_id} · {pass ? "no error" : <span className="font-mono">{r.error_signature}</span>}
      </div>
      <div className="mt-1 text-xs text-grey-600">
        Pre-execution predicted risk: <b className={pass ? "font-semibold text-black" : "font-semibold text-red-600"}>{pct(risk)}</b> ({st.label})
      </div>
    </div>
  );
}

function RunPicker({ value, onChange, runs }: { value: string; onChange: (v: string) => void; runs: any[] }) {
  const options = runs.map((r) => r.run_id);
  if (value && !options.includes(value)) options.unshift(value);
  return <Select value={value} options={options} onValueChange={onChange} />;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-2xs font-medium uppercase tracking-wide text-grey-500">{label}</span>
      {children}
    </label>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-grey-500">{children}</div>;
}
