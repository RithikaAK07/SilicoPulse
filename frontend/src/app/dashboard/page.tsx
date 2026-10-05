"use client";
import { useMemo } from "react";
import { Activity, AlertOctagon, CheckCircle2, Gauge, RotateCcw, Sparkles, Waves } from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useMeta, useOverview, useSummary } from "@/lib/api";
import { useFilters } from "@/lib/store";
import { INK, SERIES, STATUS, STATUS_INK, num, pct } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
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
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
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

          <Card className="mt-4 border-sky-200 bg-gradient-to-br from-white to-sky-50">
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-sky-600" /> GenAI Executive Summary
                </CardTitle>
                <CardDescription>Synthesizes all executions and log lines into a presentation-ready brief.</CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <SourceBadge source={summary.data?.source} />
                <Button size="sm" onClick={() => summary.refetch()} disabled={summary.isFetching}>
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
                <p className="text-sm text-slate-500">Click <b>Generate summary</b> for an instant AI brief of pass rate, top drivers, determinism and root causes.</p>
              )}
            </CardContent>
          </Card>

          <div className="mt-4 grid gap-4 xl:grid-cols-2">
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
                        <stop offset="0%" stopColor={SERIES[1]} stopOpacity={0.35} />
                        <stop offset="100%" stopColor={SERIES[1]} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke={INK.grid} vertical={false} />
                    <XAxis dataKey="day" {...axisProps} minTickGap={40} />
                    <YAxis {...axisProps} tickFormatter={(v) => pct(v, 0)} />
                    <Tooltip content={<ChartTooltip fmt={(v: number) => pct(v)} />} cursor={{ stroke: "#94a3b8" }} />
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
                    <Tooltip content={<ChartTooltip fmt={(v: number) => `${num(v)} MB/s`} />} cursor={{ stroke: "#94a3b8" }} />
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
                  <BarChart data={data.signatures} layout="vertical" margin={{ left: 70, right: 24 }} barCategoryGap={4}>
                    <CartesianGrid stroke={INK.grid} horizontal={false} />
                    <XAxis type="number" {...axisProps} />
                    <YAxis type="category" dataKey="signature" {...axisProps} width={160} />
                    <Tooltip content={<ChartTooltip fmt={(v: number) => num(v)} />} cursor={{ fill: "#e2e8f0aa" }} />
                    <Bar isAnimationActive={false} dataKey="count" name="Failures" fill={SERIES[0]} radius={[0, 4, 4, 0]} label={{ position: "right", fill: INK.muted, fontSize: 11 }} />
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
                    <Tooltip content={<ChartTooltip fmt={(v: number) => pct(v)} />} cursor={{ fill: "#e2e8f0aa" }} />
                    <Bar isAnimationActive={false} dataKey="fail_rate" name="Failure rate" fill={SERIES[0]} radius={[4, 4, 0, 0]} maxBarSize={56} label={{ position: "top", fill: INK.secondary, fontSize: 11, formatter: (v: number) => pct(v) }} />
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

function FilterBar() {
  const { data: meta } = useMeta();
  const f = useFilters();
  const days = useMemo(() => {
    if (!meta) return [] as string[];
    const out: string[] = [];
    const d = new Date(meta.filters.date_min + "T00:00:00Z");
    const end = new Date(meta.filters.date_max + "T00:00:00Z");
    while (d <= end) {
      out.push(d.toISOString().slice(0, 10));
      d.setUTCDate(d.getUTCDate() + 1);
    }
    return out;
  }, [meta]);
  if (!meta) return null;
  const fromIdx = f.dateFrom ? days.indexOf(f.dateFrom) : 0;
  const toIdx = f.dateTo ? days.indexOf(f.dateTo) : days.length - 1;
  return (
    <Card className="mb-4 p-3">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[1fr_1fr_1fr_2fr_auto] xl:items-end">
        <Labeled label="Environment">
          <Select value={f.environment} placeholder="All environments" options={meta.filters.environment} onValueChange={(v) => f.set({ environment: v })} />
        </Labeled>
        <Labeled label="Hardware">
          <Select value={f.hardware} placeholder="All hardware" options={meta.filters.hardware} onValueChange={(v) => f.set({ hardware: v })} />
        </Labeled>
        <Labeled label="Workload pattern">
          <Select value={f.workload} placeholder="All workloads" options={meta.filters.workload} onValueChange={(v) => f.set({ workload: v })} />
        </Labeled>
        <Labeled label={`Date range · ${days[fromIdx]} → ${days[toIdx]}`}>
          <div className="grid grid-cols-2 gap-3 pt-2">
            <input
              type="range"
              aria-label="Start date"
              min={0}
              max={days.length - 1}
              value={fromIdx}
              onChange={(e) => {
                const i = Math.min(+e.target.value, toIdx);
                f.set({ dateFrom: i === 0 ? "" : days[i] });
              }}
            />
            <input
              type="range"
              aria-label="End date"
              min={0}
              max={days.length - 1}
              value={toIdx}
              onChange={(e) => {
                const i = Math.max(+e.target.value, fromIdx);
                f.set({ dateTo: i === days.length - 1 ? "" : days[i] });
              }}
            />
          </div>
        </Labeled>
        <Button variant="ghost" size="sm" onClick={f.reset}>
          <RotateCcw className="h-4 w-4" /> Reset
        </Button>
      </div>
    </Card>
  );
}

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}
