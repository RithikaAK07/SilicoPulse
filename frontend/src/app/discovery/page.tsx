"use client";
import { Download, FileJson } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, ResponsiveContainer, Scatter, ComposedChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { useDiscovery } from "@/lib/api";
import { INK, SERIES, STATUS_INK, exportCSV, exportJSON, num, pct, TOKENS } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ChartTooltip, ErrorState, Loading, PageHeader, axisProps } from "@/components/common";
import { StrengthBadge, fmtP } from "@/components/evidence";

export default function DiscoveryPage() {
  const { data, error, isLoading } = useDiscovery();
  if (error) return <ErrorState error={error} />;
  return (
    <>
      <PageHeader
        badge="Configuration Discovery"
        title="Configuration Discovery & Pareto Analytics"
        subtitle="Which settings drive success or failure, and which configurations deliver the best throughput–stability trade-off."
      />
      {isLoading || !data ? <Loading /> : <Content data={data} />}
    </>
  );
}

function Content({ data }: { data: any }) {
  const pareto = data.pareto.filter((p: any) => p.pareto).sort((a: any, b: any) => a.fail_rate - b.fail_rate);
  const dominated = data.pareto.filter((p: any) => !p.pareto);
  const keyCols: string[] = (data.key_params ?? []).slice(0, 9);
  return (
    <div className="grid gap-6">
      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Feature influence on pass/fail</CardTitle>
              <CardDescription>
                Blended score: Random Forest importance (60%) + mutual information (40%). Model AUC {data.model_auc.toFixed(3)}.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="h-[440px]">
            <ResponsiveContainer>
              <BarChart data={data.importance.slice(0, 16)} layout="vertical" margin={{ left: 40, right: 36 }} barCategoryGap={3}>
                <CartesianGrid stroke={INK.grid} horizontal={false} />
                <XAxis type="number" {...axisProps} domain={[0, 1]} />
                <YAxis type="category" dataKey="feature" {...axisProps} width={150} interval={0} />
                <Tooltip
                  cursor={{ fill: "rgba(14,14,16,0.04)" }}
                  content={({ active, payload }: any) =>
                    active && payload?.length ? (
                      <div className="chart-tip">
                        <div className="mb-1 font-medium text-black">{payload[0].payload.feature}</div>
                        <div className="text-grey-800">Influence score <b className="text-black">{payload[0].payload.score.toFixed(3)}</b></div>
                        <div className="text-grey-800">RF importance {payload[0].payload.rf_importance.toFixed(4)}</div>
                        <div className="text-grey-800">Mutual information {payload[0].payload.mutual_info.toFixed(4)}</div>
                        <div className="text-grey-800">Correlation with failure {payload[0].payload.correlation > 0 ? "+" : ""}{payload[0].payload.correlation.toFixed(3)}</div>
                      </div>
                    ) : null
                  }
                />
                <Bar isAnimationActive={false} dataKey="score" name="Influence" radius={[0, 4, 4, 0]} label={{ position: "right", fill: INK.muted, fontSize: 12, formatter: (v: number) => v.toFixed(2) }}>
                  {data.importance.slice(0, 16).map((r: any) => (
                    <Cell key={r.feature} fill={r.named ? SERIES[0] : TOKENS.grey400} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
          <div className="flex gap-4 px-5 pb-4 text-xs text-grey-500">
            <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: SERIES[0] }} /> Named hardware/firmware knob</span>
            <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-grey-400" /> Generic config flag</span>
          </div>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Pareto frontier — throughput vs failure rate</CardTitle>
              <CardDescription>
                Each dot is a configuration profile (≥5 runs). {pareto.length} profiles are Pareto-optimal: none beats them on both axes.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="h-[440px]">
            <ResponsiveContainer>
              <ComposedChart margin={{ left: 0, right: 12, bottom: 12 }}>
                <CartesianGrid stroke={INK.grid} />
                <XAxis type="number" dataKey="fail_rate" name="Failure rate" {...axisProps} tickFormatter={(v) => pct(v, 0)} domain={[0, 1]}
                  label={{ value: "Failure rate →", position: "insideBottom", offset: -6, fill: INK.muted, fontSize: 12 }} />
                <YAxis type="number" dataKey="throughput" name="Throughput" {...axisProps} tickFormatter={(v) => num(v)}
                  label={{ value: "MB/s", angle: -90, position: "insideLeft", fill: INK.muted, fontSize: 12 }} />
                <ZAxis range={[40, 40]} />
                <Tooltip
                  cursor={{ strokeDasharray: "3 3", stroke: "#A1A1AA" }}
                  content={({ active, payload }: any) => {
                    const p = active && payload?.[0]?.payload;
                    return p ? (
                      <div className="chart-tip">
                        <div className="font-medium text-black">{p.config_id} {p.pareto && <span className="text-red-400">· Pareto</span>}</div>
                        <div>Failure rate {pct(p.fail_rate)} · {num(p.throughput)} MB/s</div>
                        <div>{p.runs} runs · instability {p.instability}</div>
                      </div>
                    ) : null;
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 12, color: INK.secondary }} verticalAlign="top" height={28} />
                <Scatter isAnimationActive={false} name="Dominated profiles" data={dominated} fill={TOKENS.grey300} />
                <Line isAnimationActive={false} name="Pareto frontier" data={pareto} dataKey="throughput" stroke={TOKENS.black} strokeWidth={2} dot={{ r: 5, fill: TOKENS.red600, stroke: INK.surface, strokeWidth: 2 }} />
              </ComposedChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Best-performing configuration matrix</CardTitle>
            <CardDescription>Ranked by throughput × (1 − failure rate)² × stability. Export for downstream test planning.</CardDescription>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => exportCSV("top_configurations.csv", data.top_configs)}>
              <Download className="h-4 w-4" /> CSV
            </Button>
            <Button variant="outline" size="sm" onClick={() => exportJSON("top_configurations.json", data.top_configs)}>
              <FileJson className="h-4 w-4" /> JSON
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="max-h-[420px] overflow-auto rounded-lg border border-grey-200">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-white text-grey-500">
                <tr>
                  {["#", "Config", "Runs", "Fail rate", "MB/s", "Instability", ...keyCols].map((h) => (
                    <th key={h} className="whitespace-nowrap px-3 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {data.top_configs.map((r: any, i: number) => (
                  <tr key={r.config_id} className="border-t border-grey-200 hover:bg-grey-50">
                    <td className="px-3 py-2 text-grey-500">{i + 1}</td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-black">
                      {r.config_id} {r.pareto && <Badge className="ml-1 border-red-200 bg-red-50 text-red-900" color={TOKENS.red600}>Pareto</Badge>}
                    </td>
                    <td className="px-3 py-2">{r.runs}</td>
                    <td className="px-3 py-2" style={{ color: r.fail_rate < 0.05 ? STATUS_INK.good : r.fail_rate < 0.15 ? STATUS_INK.warning : STATUS_INK.serious }}>{pct(r.fail_rate)}</td>
                    <td className="px-3 py-2 text-black">{num(r.throughput)}</td>
                    <td className="px-3 py-2">{r.instability.toFixed(3)}</td>
                    {keyCols.map((c) => (
                      <td key={c} className="whitespace-nowrap px-3 py-2">{String(r[c])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <PairTable title="Optimal parameter pairs" desc="Two-way settings with the best throughput × reliability (≥60 runs each)." rows={data.pairs.best} good />
        <PairTable title="Toxic parameter pairs" desc={`Interactions with the highest failure rate (baseline ${pct(data.pairs.baseline_fail_rate)}).`} rows={data.pairs.worst} />
      </div>
    </div>
  );
}

function PairTable({ title, desc, rows, good }: { title: string; desc: string; rows: any[]; good?: boolean }) {
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{desc}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
        <table className="w-full min-w-[30rem] text-xs">
          <thead className="text-grey-500">
            <tr>
              <th className="py-1.5 text-left font-medium">Setting A</th>
              <th className="py-1.5 text-left font-medium">Setting B</th>
              <th className="py-1.5 text-right font-medium">Runs</th>
              <th className="py-1.5 text-right font-medium">Fail rate</th>
              <th className="py-1.5 text-right font-medium">{good ? "MB/s" : "Lift"}</th>
              {!good && <th className="py-1.5 text-right font-medium">p</th>}
              {!good && <th className="py-1.5 pl-2 text-left font-medium">Evidence</th>}
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-grey-200">
                <td className="py-1.5"><code className="text-black">{r.a}</code>=<b>{String(r.a_val)}</b></td>
                <td className="py-1.5"><code className="text-black">{r.b}</code>=<b>{String(r.b_val)}</b></td>
                <td className="py-1.5 text-right">{r.runs}</td>
                <td className="py-1.5 text-right" style={{ color: good ? STATUS_INK.good : STATUS_INK.critical }}>{pct(r.fail_rate)}</td>
                <td className="py-1.5 text-right">{good ? num(r.throughput) : `${r.lift}×`}</td>
                {!good && <td className="py-1.5 text-right">{fmtP(r.p_value)}</td>}
                {!good && <td className="py-1.5 pl-2"><StrengthBadge strength={r.evidence_strength} /></td>}
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </CardContent>
    </Card>
  );
}
