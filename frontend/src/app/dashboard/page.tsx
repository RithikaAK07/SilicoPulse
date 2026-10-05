"use client";
import { Activity, AlertOctagon, CheckCircle2, Gauge, Sparkles, Waves } from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useOverview, useSummary } from "@/lib/api";
import { BAR, INK, SERIES, STATUS, STATUS_INK, num, pct } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FilterBar } from "@/components/filter-bar";
import { DataQualityStrip, TopInsights } from "@/components/evidence";
import { ChartTooltip, ErrorState, KpiCard, Loading, Markdown, PageHeader, SourceBadge, Spinner, axisProps } from "@/components/common";

export default function DashboardPage() {
  const { data, error, isLoading } = useOverview();
  const summary = useSummary();
  if (error) return <ErrorState error={error} />;
  const k = data?.kpis;
  return (
    <>
      <PageHeader
        badge="Executive Summary"
        title="SilicoPulse Intelligence Overview"
        subtitle="Campaign health across every execution, configuration profile and random seed. Filters apply to KPIs and charts below."
      />
      <FilterBar />
      {isLoading || !k ? (
        <Loading rows={2} />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5 xl:gap-6">
            <KpiCard label="Total Executions" value={num(k.total_executions)} sub={`${k.unique_configs} profiles · ${k.unique_seeds} seeds`} icon={Activity} accent={SERIES[0]} />
            <KpiCard
              label="Pass / Fail Ratio"
              value={pct(k.pass_rate)}
              sub={
                <>
                  <span style={{ color: STATUS_INK.good }}>{num(k.passes)} pass</span> · <span style={{ color: STATUS_INK.critical }}>{num(k.failures)} fail</span>
                </>
              }
              icon={CheckCircle2}
              accent={STATUS.good}
            />
            <KpiCard label="Mean Throughput" value={k.mean_throughput == null ? "–" : `${num(k.mean_throughput)} MB/s`} sub={k.p99_latency == null ? "p99 latency not in dataset" : `p99 latency ${k.p99_latency} ms`} icon={Gauge} accent={SERIES[2]} />
            <KpiCard label="High-Risk Configs" value={num(k.high_risk_configs)} sub="profiles failing > 30%" icon={AlertOctagon} accent={STATUS.critical} />
            <KpiCard label="Avg Instability Index" value={k.instability_index == null ? "–" : k.instability_index.toFixed(3)} sub={k.instability_index == null ? "not in dataset" : "0 = stable · 1 = chaotic"} icon={Waves} accent={SERIES[3]} />
          </div>
          <DataQualityStrip />

          <Card className="mt-6 border-grey-200 bg-grad-banner">
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Sparkles className="h-5 w-5 text-red-600" strokeWidth={1.75} /> GenAI Executive Summary
                </CardTitle>
                <CardDescription>Synthesizes all executions and log lines into a presentation-ready brief.</CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <SourceBadge source={summary.data?.source} />
                <Button variant="primary" size="sm" onClick={() => summary.refetch()} disabled={summary.isFetching}>
                  {summary.isFetching ? <Spinner /> : <Sparkles className="h-4 w-4" />}
                  {summary.data ? "Regenerate" : "Generate summary"}
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {summary.isFetching ? (
                <Spinner label="Synthesizing insights from the full dataset…" />
              ) : summary.data ? (
                <Markdown>{summary.data.markdown}</Markdown>
              ) : summary.error ? (
                <p className="text-sm text-red-700">{String(summary.error)}</p>
              ) : (
                <p className="text-sm text-grey-500">Click <b>Generate summary</b> for an instant AI brief of pass rate, top drivers, determinism and root causes.</p>
              )}
            </CardContent>
          </Card>

          <TopInsights />

          <div className="mt-6 grid gap-6 xl:grid-cols-2">
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Daily failure rate</CardTitle>
                  <CardDescription>Share of executions failing per day</CardDescription>
                </div>
              </CardHeader>
              <CardContent className="h-64">
                <ResponsiveContainer>
                  <AreaChart data={data.trend} margin={{ left: -10, right: 8 }}>
                    <defs>
                      <linearGradient id="fr" x1="0" x2="0" y1="0" y2="1">
                        <stop offset="0%" stopColor={SERIES[1]} stopOpacity={0.18} />
                        <stop offset="100%" stopColor={SERIES[1]} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke={INK.grid} vertical={false} />
                    <XAxis dataKey="day" {...axisProps} minTickGap={40} />
                    <YAxis {...axisProps} tickFormatter={(v) => pct(v, 0)} />
                    <Tooltip content={<ChartTooltip fmt={(v: number) => pct(v)} />} cursor={{ stroke: "#A1A1AA" }} />
                    <Area isAnimationActive={false} dataKey="fail_rate" name="Failure rate" stroke={SERIES[1]} strokeWidth={2} fill="url(#fr)" activeDot={{ r: 4, stroke: INK.surface, strokeWidth: 2 }} />
                  </AreaChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Daily mean throughput</CardTitle>
                  <CardDescription>Passing runs only, MB/s</CardDescription>
                </div>
              </CardHeader>
              <CardContent className="h-64">
                <ResponsiveContainer>
                  <LineChart data={data.trend} margin={{ left: 0, right: 8 }}>
                    <CartesianGrid stroke={INK.grid} vertical={false} />
                    <XAxis dataKey="day" {...axisProps} minTickGap={40} />
                    <YAxis {...axisProps} tickFormatter={(v) => num(v)} />
                    <Tooltip content={<ChartTooltip fmt={(v: number) => `${num(v)} MB/s`} />} cursor={{ stroke: "#A1A1AA" }} />
                    <Line isAnimationActive={false} dataKey="tput" name="Throughput" stroke={SERIES[0]} strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: INK.surface, strokeWidth: 2 }} />
                  </LineChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Failures by error signature</CardTitle>
                  <CardDescription>Count of failing executions per fingerprint</CardDescription>
                </div>
              </CardHeader>
              <CardContent className="h-72">
                <ResponsiveContainer>
                  <BarChart data={data.signatures} layout="vertical" margin={{ left: 70, right: 48 }} barCategoryGap={4}>
                    <CartesianGrid stroke={INK.grid} horizontal={false} />
                    <XAxis type="number" {...axisProps} />
                    <YAxis type="category" dataKey="signature" {...axisProps} width={160} />
                    <Tooltip content={<ChartTooltip fmt={(v: number) => num(v)} />} cursor={{ fill: "rgba(14,14,16,0.04)" }} />
                    <Bar isAnimationActive={false} dataKey="count" name="Failures" fill={BAR.base} radius={[0, 4, 4, 0]} label={{ position: "right", fill: INK.muted, fontSize: 12 }}>
                      {data.signatures.map((x: any, i: number) => (
                        <Cell key={x.signature} fill={i === 0 ? BAR.highlight : BAR.base} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Failure rate by hardware</CardTitle>
                  <CardDescription>Share of executions failing per device family</CardDescription>
                </div>
              </CardHeader>
              <CardContent className="h-72">
                <ResponsiveContainer>
                  <BarChart data={data.by_hardware} margin={{ left: -10, right: 8, top: 20 }}>
                    <CartesianGrid stroke={INK.grid} vertical={false} />
                    <XAxis dataKey="hardware" {...axisProps} />
                    <YAxis {...axisProps} tickFormatter={(v) => pct(v, 0)} />
                    <Tooltip content={<ChartTooltip fmt={(v: number) => pct(v)} />} cursor={{ fill: "rgba(14,14,16,0.04)" }} />
                    <Bar isAnimationActive={false} dataKey="fail_rate" name="Failure rate" fill={BAR.base} radius={[4, 4, 0, 0]} maxBarSize={56} label={{ position: "top", fill: INK.secondary, fontSize: 12, formatter: (v: number) => pct(v) }}>
                      {data.by_hardware.map((h: any) => (
                        <Cell key={h.hardware} fill={h.fail_rate === Math.max(...data.by_hardware.map((x: any) => x.fail_rate)) ? BAR.highlight : BAR.base} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </>
  );
}
