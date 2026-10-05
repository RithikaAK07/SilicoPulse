"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Database, Download, Zap } from "lucide-react";
import { api, post, useMeta } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { exportCSV, num } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ErrorState, PageHeader, Spinner } from "@/components/common";

const PRESETS = [
  { label: "Quick demo", n_runs: 2_000, n_config: 100, n_random: 51 },
  { label: "Hackathon spec", n_runs: 10_000, n_config: 100, n_random: 51 },
  { label: "Stress", n_runs: 50_000, n_config: 150, n_random: 64 },
];

export default function GeneratorPage() {
  const qc = useQueryClient();
  const meta = useMeta();
  const { can, refreshDataset } = useAuth();
  const [form, setForm] = useState({ n_runs: 10_000, n_config: 100, n_random: 51, seed: 42 });
  const sample = useQuery({ queryKey: ["sample"], queryFn: () => api<any[]>("/api/dataset/sample?limit=50") });
  const gen = useMutation({
    mutationFn: () => post("/api/dataset/generate", form),
    onSuccess: async () => {
      await qc.invalidateQueries();
      await refreshDataset();
    },
  });
  if (meta.error) return <ErrorState error={meta.error} />;
  const m = meta.data;
  const cols = sample.data?.[0] ? Object.keys(sample.data[0]).slice(0, 28) : [];
  return (
    <>
      <PageHeader
        badge="Innovation · Synthetic data"
        title="Synthetic Log & Telemetry Dataset Generator"
        subtitle="Generate a fresh, realistic test campaign in one click: executions, configuration flags, randomized seeds and perturbations, telemetry, pass/fail outcomes and multi-line log traces. All models retrain automatically."
      />
      <div className="grid gap-4 xl:grid-cols-[1fr_1.4fr]">
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <Zap className="h-4 w-4 text-amber-700" /> Generate dataset
              </CardTitle>
              <CardDescription>A hidden ground-truth failure model mixes deterministic config interactions with stochastic seed, thermal and jitter effects.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <div className="mb-4 flex flex-wrap gap-2">
              {PRESETS.map((p) => (
                <Button key={p.label} variant="outline" size="sm" onClick={() => setForm({ ...form, n_runs: p.n_runs, n_config: p.n_config, n_random: p.n_random })}>
                  {p.label}
                </Button>
              ))}
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <NumField label="Executions" value={form.n_runs} min={500} max={100000} onChange={(v) => setForm({ ...form, n_runs: v })} />
              <NumField label="Configuration parameters" value={form.n_config} min={13} max={400} onChange={(v) => setForm({ ...form, n_config: v })} />
              <NumField label="Randomized variables" value={form.n_random} min={6} max={200} onChange={(v) => setForm({ ...form, n_random: v })} />
              <NumField label="Generator seed" value={form.seed} min={0} max={999999} onChange={(v) => setForm({ ...form, seed: v })} />
            </div>
            <Button size="lg" className="mt-5 w-full" onClick={() => gen.mutate()} disabled={gen.isPending || !can("generate")}>
              {gen.isPending ? <Spinner label="Generating data & retraining models…" /> : <><Database className="h-4 w-4" /> Generate {num(form.n_runs)} executions</>}
            </Button>
            {gen.error && <p className="mt-3 text-sm text-red-700">{String(gen.error)}</p>}
            {gen.data && (
              <div className="mt-4 flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  Generated {num((gen.data as any).n_runs)} executions in {(gen.data as any).generation_seconds}s and retrained all models in{" "}
                  {(gen.data as any).training_seconds}s (AUC {(gen.data as any).model.auc.toFixed(3)}). Every dashboard now reflects the new dataset.
                </span>
              </div>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Active dataset</CardTitle>
              <CardDescription>{m ? `Generated ${new Date(m.generated_at).toLocaleString()}` : "Loading…"}</CardDescription>
            </div>
            {sample.data && (
              <Button variant="outline" size="sm" onClick={() => exportCSV("executions_sample.csv", sample.data!)}>
                <Download className="h-4 w-4" /> Sample CSV
              </Button>
            )}
          </CardHeader>
          <CardContent>
            {m && (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {[
                  ["Executions", num(m.n_runs)],
                  ["Config parameters", num(m.n_config_params)],
                  ["Randomized variables", num(m.n_random_vars)],
                  ["Config profiles", num(m.n_profiles)],
                  ["Log lines", num(m.log_lines)],
                  ["ML engine", m.model.engine],
                ].map(([k, v]) => (
                  <div key={k} className="rounded-lg border border-slate-200 p-3">
                    <div className="text-[11px] text-slate-500">{k}</div>
                    <div className="text-sm font-semibold">{v}</div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
      <Card className="mt-4">
        <CardHeader>
          <div>
            <CardTitle>Data preview</CardTitle>
            <CardDescription>First 50 executions (first 28 columns; log traces omitted).</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          {sample.isLoading ? (
            <Spinner label="Loading sample…" />
          ) : (
            <div className="max-h-96 overflow-auto rounded-lg border border-slate-200">
              <table className="text-[11px]">
                <thead className="sticky top-0 bg-white text-slate-500">
                  <tr>
                    {cols.map((c) => (
                      <th key={c} className="whitespace-nowrap px-2.5 py-1.5 text-left font-medium">{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {sample.data?.map((r, i) => (
                    <tr key={i} className="border-t border-slate-200">
                      {cols.map((c) => (
                        <td key={c} className={`whitespace-nowrap px-2.5 py-1 ${c === "outcome" ? (r[c] === "fail" ? "text-red-700" : "text-emerald-700") : "text-slate-700"}`}>
                          {typeof r[c] === "number" && !Number.isInteger(r[c]) ? r[c].toFixed(3) : String(r[c])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}

function NumField({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (v: number) => void }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Math.max(min, Math.min(max, +e.target.value || min)))}
        className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm tabular-nums focus:outline-none focus:ring-2 focus:ring-sky-500"
      />
    </label>
  );
}
