"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { Award, FlaskConical, Gauge, Upload, Wand2 } from "lucide-react";
import { post, useMeta, type Meta, type ShapItem } from "@/lib/api";
import { STATUS, cn, num, pct, riskStatus, TOKENS, rangeFill } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { ErrorState, Loading, Markdown, PageHeader, SourceBadge, Spinner } from "@/components/common";
import { ShapBars } from "@/components/shap-bars";

type Cfg = Record<string, string | number>;

function useDebounced<T>(value: T, ms = 250) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function PredictivePage() {
  const { data: meta, error, isLoading } = useMeta();
  if (error) return <ErrorState error={error} />;
  return (
    <>
      <PageHeader
        badge="Q7 · Q8 — Predictive & Prescriptive"
        title="Predictive & Prescriptive Engine"
        subtitle="Score any configuration's failure risk before it runs, then let the optimizer recommend the next run with confidence and explainability."
      />
      {isLoading || !meta ? <Loading /> : <Engine meta={meta} />}
    </>
  );
}

function Engine({ meta }: { meta: Meta }) {
  const [cfg, setCfg] = useState<Cfg>(() => ({ ...meta.defaults }));
  const debounced = useDebounced(cfg);
  const pred = useQuery({
    queryKey: ["predict", debounced],
    queryFn: () => post("/api/predict", { config: debounced }),
    placeholderData: keepPreviousData,
  });
  const p: any = pred.data;
  const ctxKeys = ["environment", "hardware", "workload"];
  return (
    <div className="grid gap-6">
      <div className="grid gap-6 xl:grid-cols-[1.2fr_1fr]">
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <FlaskConical className="h-5 w-5 text-black" strokeWidth={1.75} /> Interactive config sandbox
              </CardTitle>
              <CardDescription>Adjust the 10 key parameters and execution context — the LightGBM + Random Forest ensemble rescores live.</CardDescription>
            </div>
            <Button variant="ghost" size="sm" onClick={() => setCfg({ ...meta.defaults })}>Reset</Button>
          </CardHeader>
          <CardContent>
            <div className="mb-4 grid gap-3 sm:grid-cols-3">
              {ctxKeys.map((k) => (
                <label key={k} className="block">
                  <span className="mb-1 block text-2xs uppercase tracking-wide text-grey-500">{k}</span>
                  <Select value={String(cfg[k])} options={meta.choices[k]} onValueChange={(v) => setCfg({ ...cfg, [k]: v })} />
                </label>
              ))}
            </div>
            <div className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
              {meta.sandbox_params.map((name) => {
                const spec = meta.key_params.find((k) => k.name === name)!;
                return <ParamControl key={name} spec={spec} value={cfg[name]} onChange={(v) => setCfg({ ...cfg, [name]: v })} />;
              })}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <Gauge className="h-5 w-5 text-black" strokeWidth={1.75} /> Live ML prediction
              </CardTitle>
              <CardDescription>Pre-execution failure probability (ensemble) and expected throughput if the run passes.</CardDescription>
            </div>
            {pred.isFetching && <Spinner />}
          </CardHeader>
          <CardContent>
            {p ? (
              <>
                <RiskGauge risk={p.failure_risk} base={p.base_rate} />
                <div className="mt-6 grid grid-cols-3 gap-2 text-center">
                  <Stat label="Expected throughput" value={`${num(p.expected_throughput)} MB/s`} />
                  <Stat label="LightGBM risk" value={pct(p.risk_gbm)} />
                  <Stat label="Random Forest risk" value={pct(p.risk_rf)} />
                </div>
                <div className="mt-5">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-grey-500">XAI · SHAP breakdown of this score</div>
                  <ShapBars items={p.shap as ShapItem[]} />
                </div>
              </>
            ) : (
              <Spinner label="Scoring…" />
            )}
          </CardContent>
        </Card>
      </div>
      <Recommender meta={meta} context={{ environment: cfg.environment, hardware: cfg.hardware, workload: cfg.workload }} onLoad={(c) => setCfg({ ...cfg, ...c })} />
    </div>
  );
}

function ParamControl({ spec, value, onChange }: { spec: Meta["key_params"][number]; value: string | number; onChange: (v: string | number) => void }) {
  const label = (
    <div className="mb-1 flex items-baseline justify-between">
      <span className="font-mono text-xs text-grey-800">{spec.name}</span>
      <span className="text-2xs text-grey-500">{spec.desc}</span>
    </div>
  );
  if (spec.kind === "bool") {
    const on = Number(value) === 1;
    return (
      <div>
        {label}
        <button
          onClick={() => onChange(on ? 0 : 1)}
          className={cn("flex h-10 w-full items-center justify-between rounded-[10px] border px-3 text-sm", on ? "border-grey-300 bg-grey-50 text-black" : "border-grey-300 bg-white text-grey-600 hover:border-grey-400")}
        >
          {on ? "Enabled" : "Disabled"}
          <span className={cn("h-5 w-9 rounded-full p-0.5 transition-colors", on ? "bg-black" : "bg-grey-300")}>
            <span className={cn("block h-4 w-4 rounded-full bg-white transition-transform", on && "translate-x-4")} />
          </span>
        </button>
      </div>
    );
  }
  if (spec.kind === "cat") {
    return (
      <div>
        {label}
        <Select value={String(value)} options={spec.choices} onValueChange={onChange} />
      </div>
    );
  }
  const choices = spec.choices as number[];
  const idx = Math.max(0, choices.indexOf(Number(value)));
  return (
    <div>
      {label}
      <div className="flex items-center gap-3">
        <input type="range" className="range flex-1" style={rangeFill(idx, 0, choices.length - 1)} min={0} max={choices.length - 1} value={idx} onChange={(e) => onChange(choices[+e.target.value])} aria-label={spec.name} />
        <span className="w-14 rounded-lg border border-grey-200 bg-grey-50 px-2 py-1 text-center font-mono text-xs tabular-nums text-black">{choices[idx]}</span>
      </div>
    </div>
  );
}

function RiskGauge({ risk, base }: { risk: number; base: number }) {
  const st = riskStatus(risk);
  return (
    <div className="rounded-xl border border-grey-200 bg-grey-50 p-5">
      <div className="flex items-end justify-between gap-3">
        <div>
          <div className="text-2xs font-semibold uppercase tracking-[0.08em] text-grey-600">Predicted failure risk</div>
          <div className="metric mt-1 text-black">{pct(risk)}</div>
        </div>
        <span className={cn("inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-semibold", st.badge)}>{st.label} risk</span>
      </div>
      <div className="relative mt-4 h-2.5 rounded-full bg-grey-200">
        <div className="h-2.5 rounded-full transition-all" style={{ width: `${Math.min(100, risk * 100)}%`, background: st.color }} />
        <div className="absolute -top-1 h-4.5 w-0.5 bg-black" style={{ left: `${base * 100}%`, height: 18 }} title="Fleet baseline" />
      </div>
      <div className="mt-1.5 text-2xs text-grey-600">Fleet baseline failure rate {pct(base)} (marker)</div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-grey-200 bg-grey-50 p-2.5">
      <div className="text-2xs text-grey-600">{label}</div>
      <div className="text-base font-semibold tabular-nums text-black">{value}</div>
    </div>
  );
}

function Recommender({ meta, context, onLoad }: { meta: Meta; context: Cfg; onLoad: (c: Cfg) => void }) {
  const [maxRisk, setMaxRisk] = useState(0.05);
  const rec = useMutation({ mutationFn: () => post("/api/recommend", { context, max_risk: maxRisk }) });
  const r: any = rec.data;
  const best = r?.recommendations?.[0];
  const ctxLabel = useMemo(() => Object.values(context).join(" · "), [context]);
  return (
    <Card className="border-grey-200 bg-grad-banner">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Award className="h-5 w-5 text-red-600" strokeWidth={1.75} /> AI-recommended configuration for the next run
          </CardTitle>
          <CardDescription>
            Searches 4,000 candidate configurations for {ctxLabel}, maximizing throughput × reliability under a risk ceiling, then refines influential flags.
          </CardDescription>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-grey-500">
            Risk ceiling
            <input type="range" className="range" style={rangeFill(maxRisk, 0.01, 0.3)} min={0.01} max={0.3} step={0.01} value={maxRisk} onChange={(e) => setMaxRisk(+e.target.value)} />
            <span className="w-9 tabular-nums text-grey-800">{pct(maxRisk, 0)}</span>
          </label>
          <Button variant="primary" onClick={() => rec.mutate()} disabled={rec.isPending}>
            {rec.isPending ? <Spinner /> : <Wand2 className="h-4 w-4" />} Recommend
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {rec.isPending && <Spinner label="Optimizing over the configuration space…" />}
        {rec.error && <p className="text-sm text-red-700">{String(rec.error)}</p>}
        {!r && !rec.isPending && <p className="text-sm text-grey-500">Pick the execution context in the sandbox above, set a risk ceiling, then click <b>Recommend</b>.</p>}
        {best && !rec.isPending && (
          <div className="grid gap-6 xl:grid-cols-[1fr_1fr]">
            <div>
              <div className="grid grid-cols-3 gap-2">
                <Stat label="Failure risk" value={pct(best.failure_risk)} />
                <Stat label="Expected throughput" value={`${num(best.expected_throughput)} MB/s`} />
                <div className="rounded-lg border border-grey-200 p-2">
                  <div className="text-2xs text-grey-500">Confidence</div>
                  <div className="text-sm font-semibold tabular-nums">{pct(best.confidence)}</div>
                  <div className="mt-1 h-1.5 rounded bg-grey-100">
                    <div className="h-1.5 rounded" style={{ width: `${best.confidence * 100}%`, background: best.confidence > 0.75 ? TOKENS.black : TOKENS.grey500 }} />
                  </div>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 rounded-lg border border-grey-200 p-3 text-xs sm:grid-cols-3">
                {Object.entries(best.config).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-2">
                    <span className="truncate font-mono text-grey-500">{k}</span>
                    <span className="text-black">{String(v)}</span>
                  </div>
                ))}
              </div>
              <RecommendationEvidence best={best} />
              <div className="mt-3 flex items-center justify-between">
                <SourceBadge source={r.source} />
                <Button variant="outline" size="sm" onClick={() => onLoad(best.config)}>
                  <Upload className="h-4 w-4" /> Load into sandbox
                </Button>
              </div>
              <div className="mt-3 rounded-lg border border-grey-200 bg-grey-50 p-3">
                <Markdown>{r.explanation}</Markdown>
              </div>
            </div>
            <div>
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-grey-500">SHAP explainability</div>
              <ShapBars items={r.shap} />
              <div className="mb-2 mt-5 text-xs font-semibold uppercase tracking-wide text-grey-500">Alternatives (trade-offs)</div>
              <table className="w-full text-xs">
                <thead className="text-grey-500">
                  <tr>
                    <th className="py-1 text-left font-medium">Rank</th>
                    <th className="py-1 text-right font-medium">Risk</th>
                    <th className="py-1 text-right font-medium">MB/s</th>
                    <th className="py-1 text-right font-medium">Confidence</th>
                    <th />
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {r.recommendations.slice(1).map((x: any) => (
                    <tr key={x.rank} className="border-t border-grey-200">
                      <td className="py-1.5">#{x.rank}</td>
                      <td className="py-1.5 text-right">{pct(x.failure_risk)}</td>
                      <td className="py-1.5 text-right">{num(x.expected_throughput)}</td>
                      <td className="py-1.5 text-right">{pct(x.confidence)}</td>
                      <td className="py-1.5 text-right">
                        <button className="text-black hover:underline" onClick={() => onLoad(x.config)}>load</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}


function RecommendationEvidence({ best }: { best: any }) {
  const sup = best.support;
  return (
    <div className="mt-3 space-y-2 rounded-lg border border-grey-200 p-3 text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        {sup &&
          (best.extrapolation ? (
            <Badge className="border-red-200 bg-red-50 text-red-900">Extrapolation: {sup.nearest_observed_distance} of {sup.key_parameters_compared} key settings differ from any tested config</Badge>
          ) : sup.exact_matching_runs > 0 ? (
            <Badge className="border-grey-300 bg-grey-50 text-black">Supported: {num(sup.exact_matching_runs)} identical runs in training data</Badge>
          ) : (
            <Badge className="border-grey-300 bg-grey-50 text-black">Near observed data ({sup.nearest_observed_distance} setting(s) differ)</Badge>
          ))}
        {best.pareto_optimal && <Badge>Pareto-optimal among candidates</Badge>}
        {best.uncertainty && <Badge>Model disagreement ±{pct(best.uncertainty.model_disagreement / 2)}</Badge>}
        <Link href="/insights" className="ml-auto text-red-700 hover:underline">Model validation</Link>
      </div>
      {sup && (
        <p className="text-grey-600">
          Nearest tested configuration: {num(sup.nearest_observed_runs)} runs, observed failure rate {pct(sup.nearest_observed_failure_rate)}.
        </p>
      )}
      {best.why?.length > 0 && (
        <div>
          <div className="font-semibold text-grey-800">Why this configuration</div>
          <ul className="list-disc pl-4 text-grey-600">{best.why.map((w: string) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}
      {best.tradeoffs?.length > 0 && (
        <div>
          <div className="font-semibold text-grey-800">Trade-offs</div>
          <ul className="list-disc pl-4 text-grey-600">{best.tradeoffs.map((t: string) => <li key={t}>{t}</li>)}</ul>
        </div>
      )}
    </div>
  );
}
