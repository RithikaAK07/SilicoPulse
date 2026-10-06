"use client";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Cpu, FlaskConical, Layers, ShieldAlert, Thermometer, Workflow } from "lucide-react";
import { useInsightSection, useInsights, useModelValidation } from "@/lib/api";
import { BAR, INK, SERIES, TOKENS, cn, num, pct } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ChartTooltip, ErrorState, Loading, PageHeader, Spinner, axisProps } from "@/components/common";
import { FilterBar } from "@/components/filter-bar";
import { DataQualityPanel, InsightCard, SeverityBadge, StrengthBadge, fmtEvidence, fmtP } from "@/components/evidence";

const CATS = ["all", "configuration", "environment", "failure", "randomization", "hardware", "prediction", "recommendation"];

export default function InsightsPage() {
  const ins = useInsights();
  const [cat, setCat] = useState("all");
  if (ins.error) return <ErrorState error={ins.error} />;
  const list = (ins.data?.insights ?? []).filter((i) => cat === "all" || i.category === cat);
  return (
    <>
      <PageHeader
        badge="Trust layer · Evidence-grounded intelligence"
        title="Evidence & Guardrails"
        subtitle="Every finding below is computed in Python from the active dataset (respecting the filters) with sample sizes, baselines, lifts, confidence intervals and Bonferroni-corrected significance. Findings are associations, not proven causes."
      />
      <FilterBar />
      <div className="grid gap-6">
        <DataQualityPanel />
        <section>
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            {CATS.map((c) => (
              <button
                key={c}
                onClick={() => setCat(c)}
                aria-pressed={cat === c}
                className={cn("sp-sel rounded-full px-3 py-1 text-xs capitalize", cat === c ? "text-white" : "bg-white text-grey-600 ring-1 ring-grey-200 hover:bg-grey-50")}
              >
                {c}
              </button>
            ))}
            {ins.data && (
              <span className="ml-auto text-xs text-grey-500">
                {num(ins.data.runs)} runs analysed · baseline failure rate {pct(ins.data.baseline_failure_rate)}
              </span>
            )}
          </div>
          {ins.isLoading ? <Loading rows={1} /> : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {list.map((i) => <InsightCard key={i.id} ins={i} />)}
              {!list.length && <p className="text-sm text-grey-500">No insights in this category for the current filters.</p>}
            </div>
          )}
        </section>
        <Guardrails />
        <div className="grid gap-6 xl:grid-cols-2">
          <ParameterRisk />
          <Combinations />
        </div>
        <Environment />
        <Signatures />
        <Hardware />
        <ModelValidation />
      </div>
    </>
  );
}

function Section({ icon: Icon, title, desc, children }: { icon: any; title: string; desc: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2"><Icon className="h-4 w-4 text-black" /> {title}</CardTitle>
          <CardDescription>{desc}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <th className={cn("whitespace-nowrap px-2.5 py-1.5 font-medium", right ? "text-right" : "text-left")}>{children}</th>;
}
function Td({ children, right, className }: { children: React.ReactNode; right?: boolean; className?: string }) {
  return <td className={cn("px-2.5 py-1.5", right && "text-right tabular-nums", className)}>{children}</td>;
}
function Table({ head, children }: { head: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="max-h-[420px] overflow-auto rounded-lg border border-grey-200">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-white text-grey-500"><tr>{head}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function Guardrails() {
  const { data } = useInsightSection<any>("guardrails");
  return (
    <Section icon={ShieldAlert} title="Guardrails for the next campaign" desc={data?.note ?? "Discovered from data"}>
      {!data ? <Spinner /> : !data.guardrails.length ? <p className="text-sm text-grey-500">No condition passes the support and significance thresholds.</p> : (
        <Table head={<><Th>ID</Th><Th>Condition</Th><Th>Severity</Th><Th right>Failure rate</Th><Th right>Baseline</Th><Th right>Lift</Th><Th right>Runs</Th><Th right>p</Th><Th>Evidence</Th><Th>Safer observed alternative</Th></>}>
          {data.guardrails.map((g: any) => (
            <tr key={g.id} className="border-t border-grey-150 align-top">
              <Td className="font-mono text-grey-500">{g.id}</Td>
              <Td className="font-mono text-black">{g.condition}</Td>
              <Td><SeverityBadge severity={g.severity} /></Td>
              <Td right><b>{pct(g.failure_rate)}</b></Td>
              <Td right>{pct(g.baseline_failure_rate)}</Td>
              <Td right>{g.lift.toFixed(2)}×</Td>
              <Td right>{num(g.runs)}</Td>
              <Td right>{fmtP(g.p_value)}</Td>
              <Td><StrengthBadge strength={g.evidence_strength} /></Td>
              <Td className="text-grey-600">{g.safer_alternative ? <><span className="font-mono">{g.safer_alternative.condition}</span> · {pct(g.safer_alternative.failure_rate)} (n={num(g.safer_alternative.runs)})</> : "–"}</Td>
            </tr>
          ))}
        </Table>
      )}
    </Section>
  );
}

function ParameterRisk() {
  const { data } = useInsightSection<any>("parameters");
  return (
    <Section icon={Layers} title="High-risk parameter analysis" desc={data ? `Values compared with all other runs (n ≥ ${data.min_group_samples}); ${data.note}` : "…"}>
      {!data ? <Spinner /> : (
        <Table head={<><Th>Parameter</Th><Th right>Importance</Th><Th right>Corr.</Th><Th right>Cramér V</Th><Th right>χ² p</Th><Th>Riskiest value</Th><Th right>Fail rate</Th><Th right>Lift</Th><Th right>n</Th></>}>
          {data.parameters.slice(0, 15).map((p: any) => (
            <tr key={p.parameter} className="border-t border-grey-150">
              <Td className="font-mono text-black">{p.parameter}</Td>
              <Td right>{p.importance.toFixed(3)}</Td>
              <Td right>{p.correlation > 0 ? "+" : ""}{p.correlation.toFixed(3)}</Td>
              <Td right>{p.cramers_v.toFixed(3)}</Td>
              <Td right>{fmtP(p.chi2_p_value)}</Td>
              <Td className="font-mono">{String(p.riskiest_value.value)}</Td>
              <Td right><b>{pct(p.riskiest_value.failure_rate)}</b></Td>
              <Td right>{p.riskiest_value.lift.toFixed(2)}×</Td>
              <Td right>{num(p.riskiest_value.runs)}</Td>
            </tr>
          ))}
        </Table>
      )}
    </Section>
  );
}

function Combinations() {
  const { data } = useInsightSection<any>("pairs");
  return (
    <Section icon={Workflow} title="Toxic parameter combinations" desc={data ? `Min support ${data.min_support} runs · ${num(data.combinations_tested)} combinations tested · ${data.correction}` : "…"}>
      {!data ? <Spinner /> : (
        <Table head={<><Th>Combination</Th><Th right>Runs</Th><Th right>Fail rate</Th><Th right>Lift</Th><Th right>Odds ratio</Th><Th right>Interaction</Th><Th>Evidence</Th></>}>
          {data.toxic.map((t: any) => (
            <tr key={t.condition} className="border-t border-grey-150">
              <Td className="font-mono text-black">{t.condition}</Td>
              <Td right>{num(t.runs)}</Td>
              <Td right><b>{pct(t.failure_rate)}</b></Td>
              <Td right>{t.lift.toFixed(2)}×</Td>
              <Td right>{t.odds_ratio.toFixed(1)}</Td>
              <Td right>{t.interaction_lift.toFixed(2)}×</Td>
              <Td><StrengthBadge strength={t.evidence_strength} /></Td>
            </tr>
          ))}
        </Table>
      )}
    </Section>
  );
}

function Environment() {
  const { data } = useInsightSection<any>("environment");
  return (
    <Section icon={Thermometer} title="Environmental thresholds & drift" desc="Risk thresholds are learned per variable (best-separating percentile cut, Bonferroni-corrected) — nothing is assumed in advance.">
      {!data ? <Spinner /> : (
        <Table head={<><Th>Learned condition</Th><Th right>Runs beyond</Th><Th right>Fail rate beyond</Th><Th right>Fail rate within</Th><Th right>Lift</Th><Th right>z</Th><Th>Evidence</Th><Th>Drift (share beyond, first → last quarter)</Th></>}>
          {data.thresholds.slice(0, 12).map((t: any) => {
            const d = (data.drift ?? []).find((x: any) => x.variable === t.variable);
            return (
              <tr key={t.variable} className="border-t border-grey-150">
                <Td className="font-mono text-black">{t.condition}</Td>
                <Td right>{num(t.risky_runs)}</Td>
                <Td right><b>{pct(t.risky_failure_rate)}</b></Td>
                <Td right>{pct(t.safe_failure_rate)}</Td>
                <Td right>{t.lift.toFixed(2)}×</Td>
                <Td right>{t.z_score}</Td>
                <Td><StrengthBadge strength={t.evidence_strength} /></Td>
                <Td className="text-grey-600">{d?.early_share_beyond_threshold != null ? `${pct(d.early_share_beyond_threshold)} → ${pct(d.late_share_beyond_threshold)}` : "–"}</Td>
              </tr>
            );
          })}
        </Table>
      )}
    </Section>
  );
}

function Signatures() {
  const { data } = useInsightSection<any>("signatures");
  return (
    <Section icon={FlaskConical} title="Failure signature hierarchy" desc={data?.hierarchy ?? "…"}>
      {!data ? <Spinner /> : (
        <div className="grid gap-3 lg:grid-cols-2">
          {data.signatures.map((s: any) => (
            <div key={s.signature} className="min-w-0 rounded-lg border border-grey-200 p-3 text-xs">
              <div className="mb-1.5 flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm font-semibold text-black">{s.signature}</span>
                <Badge>{num(s.failures)} · {pct(s.share_of_failures)} of failures</Badge>
                <Badge className="capitalize">{s.tendency}</Badge>
              </div>
              <Row label="Parameters">{s.associated_parameters.slice(0, 3).map((c: any) => `${c.condition} (${c.lift}×)`).join(" · ") || "no condition with ≥30% support"}</Row>
              <Row label="Environment">{s.associated_environment.map((e: any) => `${e.variable} d=${e.cohens_d}`).join(" · ") || "no material shift"}</Row>
              <Row label="Randomization">{s.associated_randomization.map((e: any) => `seed ${e.seed} (${e.lift}×, z=${e.z_score})`).join(" · ") || "no seed over-represented"}</Row>
              <Row label="Hardware">{s.hardware.map((h: any) => `${h.hardware} ${pct(h.share, 0)} (${h.lift}×)`).join(" · ")}</Row>
              {s.log_patterns.length > 0 && <Row label="Logs"><span className="font-mono text-2xs">{s.log_patterns[0]}</span></Row>}
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-2 py-0.5">
      <span className="text-grey-600">{label}</span>
      <span className="min-w-0 text-grey-800 [overflow-wrap:anywhere]">{children}</span>
    </div>
  );
}

function Hardware() {
  const { data } = useInsightSection<any>("hardware");
  return (
    <Section icon={Cpu} title="Hardware platform comparison" desc={data?.note ?? "…"}>
      {!data ? <Spinner /> : (
        <>
          <Table head={<><Th>Hardware</Th><Th right>Runs</Th><Th right>Fail rate</Th><Th right>95% CI</Th><Th right>Matched-config rate</Th><Th right>Throughput</Th><Th>Evidence vs rest</Th><Th>Dominant signatures</Th><Th>Most sensitive setting</Th></>}>
            {data.hardware.map((h: any) => (
              <tr key={h.hardware} className="border-t border-grey-150">
                <Td className="font-medium text-black">{h.hardware}</Td>
                <Td right>{num(h.runs)}</Td>
                <Td right><b>{pct(h.failure_rate)}</b></Td>
                <Td right>{fmtEvidence("ci95", h.ci95)}</Td>
                <Td right>{pct(h.matched_profile_failure_rate)}</Td>
                <Td right>{h.mean_throughput_pass == null ? "–" : `${num(h.mean_throughput_pass)} MB/s`}</Td>
                <Td><StrengthBadge strength={h.evidence_strength} /></Td>
                <Td className="text-grey-600">{h.dominant_signatures.map((s: any) => `${s.signature} ${pct(s.share, 0)}`).join(", ")}</Td>
                <Td className="text-grey-600">{h.config_sensitivity[0] ? `${h.config_sensitivity[0].parameter} (±${pct(h.config_sensitivity[0].failure_rate_spread, 0)})` : "–"}</Td>
              </tr>
            ))}
          </Table>
          {data.cross_tier_patterns.length > 0 && (
            <div className="mt-3 text-xs text-grey-600">
              <b className="text-grey-800">Cross-tier patterns:</b>{" "}
              {data.cross_tier_patterns.map((c: any) => `${c.condition} elevated on ${c.tiers_elevated}/${c.tiers_checked} tiers`).join(" · ")}
              {!data.execution_time_available && " · Execution-time data not available in this dataset."}
            </div>
          )}
        </>
      )}
    </Section>
  );
}

function ModelValidation() {
  const { data } = useModelValidation();
  if (!data) return <Spinner label="Loading model validation…" />;
  const ho = data.holdout;
  const tiles: [string, string][] = [
    ["Hold-out ROC-AUC", ho.roc_auc.toFixed(3)],
    ["5-fold CV ROC-AUC", `${data.cv.roc_auc_mean.toFixed(3)} ± ${data.cv.roc_auc_std.toFixed(3)}`],
    ["PR-AUC", ho.pr_auc.toFixed(3)],
    ["Precision / Recall @0.5", `${pct(ho.precision, 0)} / ${pct(ho.recall, 0)}`],
    [`F1 @${ho.best_f1_threshold}`, ho.best_f1.toFixed(3)],
    ["Brier score", ho.brier.toFixed(3)],
  ];
  const cm = ho.confusion_matrix_at_best;
  return (
    <Section icon={FlaskConical} title="Model validation (failure-risk predictor)" desc={`${data.model} · ${data.split.strategy} · test failure rate ${pct(data.class_balance.failure_rate_test)}`}>
      <div className="grid gap-6 xl:grid-cols-[1fr_1fr_1fr]">
        <div>
          <div className="grid grid-cols-2 gap-2">
            {tiles.map(([k, v]) => (
              <div key={k} className="rounded-lg border border-grey-200 p-2.5">
                <div className="text-2xs text-grey-500">{k}</div>
                <div className="text-sm font-semibold tabular-nums text-black">{v}</div>
              </div>
            ))}
          </div>
          <div className="mt-3 text-xs">
            <div className="mb-1 font-semibold text-grey-800">Confusion matrix @ threshold {ho.best_f1_threshold} (n={num(data.test_size)})</div>
            <table className="text-center tabular-nums">
              <thead><tr><th /><th className="px-3 text-grey-500">pred pass</th><th className="px-3 text-grey-500">pred fail</th></tr></thead>
              <tbody>
                <tr><td className="pr-2 text-right text-grey-500">actual pass</td><td className="rounded bg-grey-50 px-3 py-1.5">{num(cm.tn)}</td><td className="rounded bg-red-50 px-3 py-1.5">{num(cm.fp)}</td></tr>
                <tr><td className="pr-2 text-right text-grey-500">actual fail</td><td className="rounded bg-red-50 px-3 py-1.5">{num(cm.fn)}</td><td className="rounded bg-grey-50 px-3 py-1.5">{num(cm.tp)}</td></tr>
              </tbody>
            </table>
            {data.throughput_model?.r2 != null && <p className="mt-2 text-grey-500">Throughput model: R² {data.throughput_model.r2} · MAE {num(data.throughput_model.mae)} MB/s</p>}
          </div>
        </div>
        <div className="h-64">
          <div className="mb-1 text-xs font-semibold text-grey-800">Calibration (observed vs predicted failure rate)</div>
          <ResponsiveContainer>
            <LineChart data={data.calibration} margin={{ left: -10, right: 10, top: 4, bottom: 4 }}>
              <CartesianGrid stroke={INK.grid} />
              <XAxis dataKey="mean_predicted" type="number" domain={[0, 1]} {...axisProps} tickFormatter={(v) => pct(v, 0)} />
              <YAxis type="number" domain={[0, 1]} {...axisProps} tickFormatter={(v) => pct(v, 0)} />
              <Tooltip content={<ChartTooltip fmt={(v: number) => pct(v)} labelFmt={(l: number) => `predicted ${pct(l)}`} />} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line isAnimationActive={false} name="Perfect calibration" data={[{ mean_predicted: 0, observed_rate: 0 }, { mean_predicted: 1, observed_rate: 1 }]} dataKey="observed_rate" stroke={TOKENS.grey400} strokeDasharray="4 4" dot={false} />
              <Line isAnimationActive={false} name="Model" dataKey="observed_rate" stroke={SERIES[0]} strokeWidth={2} dot={{ r: 3, fill: SERIES[0], strokeWidth: 0 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="h-64">
          <div className="mb-1 text-xs font-semibold text-grey-800">Global SHAP (mean |contribution|, hold-out sample)</div>
          <ResponsiveContainer>
            <BarChart data={(data.global_shap ?? []).slice(0, 10)} layout="vertical" margin={{ left: 30, right: 16 }} barCategoryGap={3}>
              <CartesianGrid stroke={INK.grid} horizontal={false} />
              <XAxis type="number" {...axisProps} />
              <YAxis type="category" dataKey="feature" {...axisProps} width={120} interval={0} tick={{ fill: INK.muted, fontSize: 12 }} />
              <Tooltip content={<ChartTooltip fmt={(v: number) => v.toFixed(3)} />} cursor={{ fill: "rgba(14,14,16,0.04)" }} />
              <Bar isAnimationActive={false} dataKey="mean_abs_shap" name="mean |SHAP|" fill={BAR.base} radius={[0, 4, 4, 0]}>
                {(data.global_shap ?? []).slice(0, 10).map((x: any, i: number) => (
                  <Cell key={x.feature} fill={i === 0 ? BAR.highlight : BAR.base} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <p className="mt-2 text-2xs text-grey-500">CV folds: {data.cv.roc_auc_folds.join(", ")} on {num(data.cv.rows_used)} training rows.</p>
    </Section>
  );
}
