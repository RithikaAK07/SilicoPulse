"use client";
import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { useDrift, useRandomization } from "@/lib/api";
import { INK, SERIES, STATUS, STATUS_INK, num, pct } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ChartTooltip, ErrorState, Loading, PageHeader, axisProps } from "@/components/common";
import { Heatmap } from "@/components/heatmap";
import { DriftChart } from "@/components/drift-chart";
import { Topology } from "@/components/topology";

const SHAPES = ["circle", "square", "triangle", "diamond", "cross", "star", "wye"] as const;
const pretty = (k: string) => k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export default function RandomizationPage() {
  const { data, error, isLoading } = useRandomization();
  const drift = useDrift();
  if (error) return <ErrorState error={error} />;
  return (
    <>
      <PageHeader
        badge="Q3 · Q4 — Randomization & Determinism"
        title="Randomization & Determinism Engine"
        subtitle="Rank random variables by impact, expose seed-reproducible failures, and separate deterministic (config-driven) from stochastic failures."
      />
      {isLoading || !data ? <Loading /> : <Content data={data} />}
      <div className="mt-4">{drift.data ? <DriftSection d={drift.data} /> : <Loading rows={1} />}</div>
    </>
  );
}

function Content({ data }: { data: any }) {
  const perturb = Object.keys(data.heatmaps);
  const [pvSel, setPv] = useState<string | null>(null);
  const pv = pvSel && perturb.includes(pvSel) ? pvSel : perturb[0];
  const det = data.determinism;
  const classes = Object.fromEntries(det.failure_classes.map((c: any) => [c.class, c.count]));
  const detCount = classes.deterministic ?? 0;
  const stoCount = (classes.stochastic ?? 0) + (classes.stable ?? 0);
  const flagged = data.seeds.filter((s: any) => s.flag);
  const curveVars = Object.keys(data.curves);
  const hm = pv ? data.heatmaps[pv] : null;
  return (
    <div className="grid gap-4">
      <div className="grid gap-4 xl:grid-cols-[1.1fr_1fr]">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Q3 · Randomization impact ranking</CardTitle>
              <CardDescription>Impact = failure-rate swing across the variable’s range (45%) + RF importance (35%) + mutual information (20%).</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="h-[380px]">
            <ResponsiveContainer>
              <BarChart data={data.sensitivity.slice(0, 12)} layout="vertical" margin={{ left: 30, right: 40 }} barCategoryGap={3}>
                <CartesianGrid stroke={INK.grid} horizontal={false} />
                <XAxis type="number" {...axisProps} />
                <YAxis type="category" dataKey="variable" {...axisProps} width={130} interval={0} />
                <Tooltip
                  cursor={{ fill: "#e2e8f0aa" }}
                  content={({ active, payload }: any) => {
                    const p = active && payload?.[0]?.payload;
                    return p ? (
                      <div className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-700">
                        <div className="font-medium text-slate-900">{p.variable}</div>
                        <div>Impact score <b className="text-slate-900">{p.impact_score.toFixed(3)}</b></div>
                        <div>Failure-rate swing {pct(p.fail_rate_spread)}</div>
                        <div>RF importance {p.rf_importance.toFixed(4)} · MI {p.mutual_info.toFixed(4)}</div>
                      </div>
                    ) : null;
                  }}
                />
                <Bar isAnimationActive={false} dataKey="impact_score" name="Impact" fill={SERIES[0]} radius={[0, 4, 4, 0]} label={{ position: "right", fill: INK.muted, fontSize: 10, formatter: (v: number) => v.toFixed(2) }} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Sensitivity curves</CardTitle>
              <CardDescription>Failure rate across octiles of the most impactful continuous random variables.</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-3">
            {curveVars.map((v) => (
              <div key={v} className="h-40 rounded-lg border border-slate-200 p-2">
                <div className="mb-1 text-[11px] font-medium text-slate-700">{v}</div>
                <ResponsiveContainer height="85%">
                  <LineChart data={data.curves[v]} margin={{ left: -24, right: 6, top: 4 }}>
                    <CartesianGrid stroke={INK.grid} vertical={false} />
                    <XAxis dataKey="x" {...axisProps} tick={{ fill: INK.muted, fontSize: 9 }} tickFormatter={(t) => (Math.abs(t) >= 10 ? t.toFixed(0) : t.toFixed(2))} />
                    <YAxis {...axisProps} tick={{ fill: INK.muted, fontSize: 9 }} tickFormatter={(t) => pct(t, 0)} />
                    <Tooltip content={<ChartTooltip fmt={(x: number) => pct(x)} labelFmt={(l: number) => `${v} ≈ ${l}`} />} />
                    <Line isAnimationActive={false} dataKey="fail_rate" name="Failure rate" stroke={SERIES[1]} strokeWidth={2} dot={{ r: 3, fill: SERIES[1], strokeWidth: 0 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Randomization sensitivity heatmap</CardTitle>
              <CardDescription>Failure rate for the 10 riskiest and 6 safest seeds across quintile bands of an environment perturbation.</CardDescription>
            </div>
            <div className="flex gap-1">
              {perturb.map((k) => (
                <button
                  key={k}
                  onClick={() => setPv(k)}
                  className={`rounded-md px-2 py-1 text-[11px] ${pv === k ? "bg-sky-100 text-sky-700" : "text-slate-500 hover:bg-slate-100"}`}
                >
                  {pretty(k)}
                </button>
              ))}
            </div>
          </CardHeader>
          <CardContent>
            {hm ? (
              <Heatmap rows={hm.rows} cols={hm.cols} cells={hm.cells} rowLabel="Seed" />
            ) : (
              <NoSeed what="the seed × perturbation heatmap" />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Seed repeatability — observed vs expected failure rate</CardTitle>
              <CardDescription>
                Expected = configuration-only ML model. Seeds far above the diagonal (z &gt; 3) fail reproducibly regardless of config.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="h-[360px]">
            {!data.has_seed ? (
              <NoSeed what="seed repeatability scoring" />
            ) : (
            <ResponsiveContainer>
              <ScatterChart margin={{ left: 0, right: 16, bottom: 12 }}>
                <CartesianGrid stroke={INK.grid} />
                <XAxis type="number" dataKey="expected" name="Expected" {...axisProps} tickFormatter={(v) => pct(v, 0)} domain={["auto", "auto"]}
                  label={{ value: "Expected failure rate", position: "insideBottom", offset: -6, fill: INK.muted, fontSize: 11 }} />
                <YAxis type="number" dataKey="observed" name="Observed" {...axisProps} tickFormatter={(v) => pct(v, 0)} domain={["auto", "auto"]} />
                <ZAxis dataKey="runs" range={[40, 160]} />
                <ReferenceLine segment={[{ x: 0.15, y: 0.15 }, { x: 0.4, y: 0.4 }]} stroke="#475569" strokeDasharray="4 4" ifOverflow="extendDomain" />
                <Tooltip
                  cursor={{ strokeDasharray: "3 3" }}
                  content={({ active, payload }: any) => {
                    const p = active && payload?.[0]?.payload;
                    return p ? (
                      <div className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-700">
                        <div className="font-medium text-slate-900">Seed {p.seed} {p.flag && <span style={{ color: STATUS_INK.critical }}>· anomalous</span>}</div>
                        <div>Observed {pct(p.observed)} vs expected {pct(p.expected)}</div>
                        <div>Lift {p.lift}× · z = {p.z} · repeatability {p.repeatability}</div>
                        <div>{p.runs} runs</div>
                      </div>
                    ) : null;
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} verticalAlign="top" height={28} />
                <Scatter isAnimationActive={false} name="Normal seed" data={data.seeds.filter((s: any) => !s.flag)} fill={SERIES[0]} fillOpacity={0.7} />
                <Scatter isAnimationActive={false} name="Anomalous seed (z > 3)" data={flagged} fill={STATUS.critical} shape="diamond" />
              </ScatterChart>
            </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_1.3fr]">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Q4 · Deterministic vs stochastic failures</CardTitle>
              <CardDescription>
                Deterministic = the configuration fails ≥80% of runs on any seed. Stochastic = outcome depends on seed, thermal drift or jitter.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <div className="mb-4 grid grid-cols-2 gap-3">
              <div className="rounded-lg border border-slate-200 p-3">
                <div className="text-xs text-slate-500">Deterministic failures</div>
                <div className="text-2xl font-semibold">{num(detCount)}</div>
                <div className="text-xs text-slate-500">{pct(detCount / (detCount + stoCount))} of failures</div>
              </div>
              <div className="rounded-lg border border-slate-200 p-3">
                <div className="text-xs text-slate-500">Stochastic failures</div>
                <div className="text-2xl font-semibold">{num(stoCount)}</div>
                <div className="text-xs text-slate-500">{pct(stoCount / (detCount + stoCount))} of failures</div>
              </div>
            </div>
            <div className="h-64">
              <ResponsiveContainer>
                <BarChart data={det.by_signature} layout="vertical" margin={{ left: 60, right: 10 }} barCategoryGap={4}>
                  <CartesianGrid stroke={INK.grid} horizontal={false} />
                  <XAxis type="number" {...axisProps} />
                  <YAxis type="category" dataKey="error_signature" {...axisProps} width={150} interval={0} tick={{ fill: INK.muted, fontSize: 10 }} />
                  <Tooltip content={<ChartTooltip />} cursor={{ fill: "#e2e8f0aa" }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar isAnimationActive={false} dataKey="deterministic" name="Deterministic" stackId="a" fill={SERIES[1]} stroke={INK.surface} strokeWidth={2} />
                  <Bar isAnimationActive={false} dataKey="stochastic" name="Stochastic" stackId="a" fill={SERIES[0]} stroke={INK.surface} strokeWidth={2} radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Failure clustering map</CardTitle>
              <CardDescription>K-Means (k=6) on failing runs’ config + randomization + telemetry, projected to 2D with PCA. Shape and color both mark the cluster.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <div className="h-72">
              <ResponsiveContainer>
                <ScatterChart margin={{ left: -10, right: 10 }}>
                  <CartesianGrid stroke={INK.grid} />
                  <XAxis type="number" dataKey="x" name="PC1" {...axisProps} />
                  <YAxis type="number" dataKey="y" name="PC2" {...axisProps} />
                  <ZAxis range={[22, 22]} />
                  <Tooltip
                    content={({ active, payload }: any) => {
                      const p = active && payload?.[0]?.payload;
                      return p ? (
                        <div className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-700">
                          Cluster {p.cluster} · <span className="font-mono">{p.signature}</span>
                        </div>
                      ) : null;
                    }}
                  />
                  {det.clusters.summary.map((c: any) => (
                    <Scatter
                      isAnimationActive={false}
                      key={c.cluster}
                      name={`C${c.cluster}`}
                      data={det.clusters.points.filter((p: any) => p.cluster === c.cluster)}
                      fill={SERIES[c.cluster % SERIES.length]}
                      fillOpacity={0.75}
                      shape={SHAPES[c.cluster % SHAPES.length]}
                    />
                  ))}
                </ScatterChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {det.clusters.summary.map((c: any) => (
                <div key={c.cluster} className="flex items-start gap-2 rounded-lg border border-slate-200 p-2 text-[11px]">
                  <span className="mt-0.5 h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: SERIES[c.cluster % SERIES.length] }} />
                  <div>
                    <div className="text-slate-800">
                      C{c.cluster} ({SHAPES[c.cluster % SHAPES.length]}) · {num(c.size)} runs · <span className="font-mono">{c.dominant_signature}</span> ({pct(c.purity, 0)})
                    </div>
                    <div className="text-slate-500">{c.drivers.join(", ")}</div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function NoSeed({ what }: { what: string }) {
  return (
    <div className="grid h-full min-h-40 place-items-center rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
      <div>
        The active dataset has no random-seed column, so {what} is unavailable.
        <div className="mt-1 text-xs">Map a seed column on the CSV Data Upload tab to enable it.</div>
      </div>
    </div>
  );
}

const DRIFT_COLORS = [SERIES[1], SERIES[0], SERIES[2]];

function DriftSection({ d }: { d: any }) {
  const vars = (d.variables as any[]).map((v, i) => ({ ...v, color: DRIFT_COLORS[i % DRIFT_COLORS.length], label: pretty(v.label) }));
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Randomization drift visualizer</CardTitle>
            <CardDescription>How randomized environment variables drift over the campaign (daily mean with p95 band) and when the daily mean breaches its drift limit.</CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            {d.crossings.map((c: any) => (
              <Badge key={c.variable} color={c.first_crossing ? STATUS.critical : STATUS.good}>
                {c.first_crossing ? <AlertTriangle className="h-3 w-3" /> : null}
                {c.variable}: {c.first_crossing ? `crossed ${c.first_crossing} · ${c.days_above}d above` : "within limits"}
              </Badge>
            ))}
          </div>
        </CardHeader>
        <CardContent className="grid gap-4 lg:grid-cols-3">
          {!vars.length && <p className="text-sm text-slate-500">No continuous randomized variables in the active dataset.</p>}
          {vars.map((v) => (
            <div key={v.key} className="rounded-lg border border-slate-200 p-3">
              <div className="mb-2 text-xs font-medium text-slate-700">{v.label}</div>
              <div className="h-56">
                <DriftChart series={d.series} meanKey={v.key} p95Key={v.p95_key} threshold={v.limit} color={v.color} unit={v.unit} />
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Hardware topology risk map</CardTitle>
            <CardDescription>Orchestrator → environment → device fleet. Node color = failure-risk status, label = failure rate.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <Topology nodes={d.topology} />
        </CardContent>
      </Card>
    </div>
  );
}
