"use client";
import { useState } from "react";
import Link from "next/link";
import { AlertOctagon, AlertTriangle, ArrowRight, ChevronDown, CircleDot, Database, Info, ShieldCheck } from "lucide-react";
import { useDataQuality, useInsights, type Insight } from "@/lib/api";
import { cn, num, pct } from "@/lib/utils";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Badge } from "./ui/badge";
import { Spinner } from "./common";

// ---------------------------------------------------------------- formatting
export function fmtP(p: number | null | undefined) {
  if (p == null) return "–";
  if (p === 0) return "< 1e-300";
  return p < 0.001 ? p.toExponential(1) : p.toFixed(3);
}

const FRACTION_KEYS = /(rate|share|precision|recall|f1|coverage|auc|baseline|observed|expected|support|brier)/;

export function fmtEvidence(key: string, v: any): string {
  if (v == null) return "–";
  if (Array.isArray(v) && v.length === 2 && v.every((x) => typeof x === "number")) return `${pct(v[0])} – ${pct(v[1])}`;
  if (typeof v !== "number") return String(v);
  if (key.includes("p_value")) return fmtP(v);
  if (/(lift|ratio)/.test(key)) return `${v.toFixed(2)}×`;
  if (/(z_score|^z$|cramers|correlation|importance|cohens|shap)/.test(key)) return v.toFixed(3);
  if (FRACTION_KEYS.test(key) && Math.abs(v) <= 1) return pct(v);
  if (Number.isInteger(v)) return num(v);
  return v.toFixed(3);
}

const LABELS: Record<string, string> = {
  sample_size: "Sample size", runs: "Runs", failures: "Failures", failure_rate: "Failure rate", baseline_failure_rate: "Baseline",
  lift: "Lift", odds_ratio: "Odds ratio", interaction_lift: "Interaction lift", p_value: "p-value", z_score: "z-score", ci95: "95% CI",
  correlation: "Correlation", importance: "Importance", cramers_v: "Cramér's V", risk_ratio: "Risk ratio", threshold: "Threshold",
  share_of_failures: "Share of failures", deterministic_share: "Deterministic share", roc_auc: "ROC-AUC", cv_roc_auc_mean: "CV ROC-AUC",
  cv_roc_auc_std: "CV σ", precision: "Precision", recall: "Recall", f1: "F1", brier: "Brier", matched_profile_failure_rate: "Matched-config rate",
};
export const evidenceLabel = (k: string) => LABELS[k] ?? k.replace(/_/g, " ");

// ---------------------------------------------------------------- badges
/** Risk-level badges (no green/amber); the level name is always shown as text. */
const SEV = {
  critical: { icon: AlertOctagon, label: "Critical", cls: "border-red-600 bg-red-600 text-white", iconCls: "text-white" },
  high: { icon: AlertTriangle, label: "High", cls: "border-red-200 bg-red-100 text-red-900", iconCls: "text-red-700" },
  medium: { icon: Info, label: "Medium", cls: "border-grey-200 bg-grey-100 text-black", iconCls: "text-grey-500" },
  low: { icon: CircleDot, label: "Low", cls: "border-grey-300 bg-white text-black", iconCls: "text-grey-500" },
} as const;

export function SeverityBadge({ severity }: { severity: string }) {
  const s = SEV[(severity as keyof typeof SEV) ?? "low"] ?? SEV.low;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-2xs font-semibold", s.cls)}>
      <s.icon className={cn("h-3 w-3", s.iconCls)} strokeWidth={2} /> {s.label}
    </span>
  );
}

const STRENGTH_STYLE: Record<string, string> = {
  strong: "border-black bg-white text-black",
  moderate: "border-grey-300 bg-white text-black",
  weak: "border-grey-300 bg-grey-50 text-grey-600",
  "not significant": "border-grey-200 bg-grey-50 text-grey-600",
  insufficient: "border-grey-200 bg-grey-50 text-grey-600",
};

export function StrengthBadge({ strength }: { strength?: string | null }) {
  if (!strength) return null;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-2xs font-medium", STRENGTH_STYLE[strength] ?? STRENGTH_STYLE.weak)}>
      <ShieldCheck className="h-3 w-3" strokeWidth={2} /> {strength === "not significant" ? "Not significant" : `${strength[0].toUpperCase()}${strength.slice(1)} evidence`}
    </span>
  );
}

// ---------------------------------------------------------------- evidence chips
const CHIP_KEYS = ["sample_size", "runs", "failure_rate", "baseline_failure_rate", "lift", "p_value"];

export function EvidenceChips({ evidence, keys = CHIP_KEYS }: { evidence: Record<string, any>; keys?: string[] }) {
  const shown = keys.filter((k) => evidence[k] != null).slice(0, 5);
  if (!shown.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {shown.map((k) => (
        <span key={k} className="rounded-md bg-grey-100 px-2 py-0.5 text-2xs tabular-nums text-grey-800">
          <span className="text-grey-500">{evidenceLabel(k)}</span> <b className="font-semibold text-black">{fmtEvidence(k, evidence[k])}</b>
        </span>
      ))}
    </div>
  );
}

export function EvidenceTable({ evidence }: { evidence: Record<string, any> }) {
  const rows = Object.entries(evidence).filter(([, v]) => v != null && typeof v !== "object" || Array.isArray(v));
  return (
    <table className="w-full text-xs">
      <tbody className="tabular-nums">
        {rows.map(([k, v]) => (
          <tr key={k} className="border-t border-grey-150">
            <td className="py-1 pr-3 text-grey-500">{evidenceLabel(k)}</td>
            <td className="py-1 text-right font-medium text-black">{fmtEvidence(k, v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------- insight card
export function InsightCard({ ins, defaultOpen = false }: { ins: Insight; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Card className="flex flex-col">
      <CardHeader className="pb-1">
        <div className="min-w-0">
          <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
            <SeverityBadge severity={ins.severity} />
            <StrengthBadge strength={ins.evidence_strength} />
            <Badge className="capitalize">{ins.category}</Badge>
          </div>
          <CardTitle className="leading-snug">{ins.title}</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-2.5">
        <p className="text-sm text-grey-800">{ins.finding}</p>
        <EvidenceChips evidence={ins.evidence} />
        {ins.recommendation && (
          <p className="rounded-md bg-grey-50 px-2.5 py-1.5 text-xs text-black">
            <b>Action:</b> {ins.recommendation}
          </p>
        )}
        <button onClick={() => setOpen(!open)} className="mt-auto flex items-center gap-1 text-xs font-medium text-grey-500 hover:text-grey-800" aria-expanded={open}>
          <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} /> Why this insight?
        </button>
        {open && (
          <div className="rounded-lg border border-grey-200 bg-grey-50/60 p-3">
            <EvidenceTable evidence={ins.evidence} />
            {ins.explanation && <p className="mt-2 text-2xs text-grey-500">Method: {ins.explanation}</p>}
            {ins.affected_parameters?.length > 0 && <p className="mt-1 text-2xs text-grey-500">Parameters: {ins.affected_parameters.join(", ")}</p>}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------- dashboard widgets
export function TopInsights({ count = 3 }: { count?: number }) {
  const { data, isLoading } = useInsights();
  return (
    <div className="mt-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-black">Evidence-backed insights{data?.filters && Object.keys(data.filters).length ? " (filtered)" : ""}</h2>
        <Link href="/insights" className="flex items-center gap-1 text-xs font-medium text-red-700 hover:underline">
          All insights & guardrails <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
      {isLoading || !data ? (
        <Spinner label="Computing evidence…" />
      ) : (
        <div className="grid gap-3 lg:grid-cols-3">
          {data.insights.slice(0, count).map((i) => (
            <InsightCard key={i.id} ins={i} />
          ))}
        </div>
      )}
    </div>
  );
}

export function DataQualityStrip() {
  const { data } = useDataQuality();
  if (!data) return null;
  const items: [string, string][] = [
    ["Executions", num(data.total_executions)],
    ["Pass / fail", `${num(data.successful_executions)} / ${num(data.failures)}`],
    ["Config params", num(data.configuration_parameters)],
    ["Random vars", num(data.randomized_variables)],
    ["Telemetry", num(data.telemetry_variables)],
    ["Missing values", data.missing_values_note && data.source !== "benchmark" ? "not recorded" : num(data.missing_values_total)],
    ["Duplicate rows", num(data.duplicate_rows)],
    ["Log coverage", data.log_coverage == null ? "–" : pct(data.log_coverage, 0)],
    ["Timestamps", data.timestamp_source],
  ];
  return (
    <Card className="mt-4 p-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
        <span className="flex items-center gap-1.5 font-semibold text-black">
          <Database className="h-4 w-4 text-black" /> Data quality
          <Badge className={data.source === "benchmark" ? "" : "border-grey-300 bg-grey-50 text-black"}>
            {data.source === "benchmark" ? "Synthetic benchmark" : "Real uploaded data"}
          </Badge>
        </span>
        {items.map(([k, v]) => (
          <span key={k} className="tabular-nums text-grey-500">
            {k} <b className="text-black">{v}</b>
          </span>
        ))}
        <Link href="/insights#data-quality" className="ml-auto text-red-700 hover:underline">Details</Link>
      </div>
      {data.warnings?.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-red-900">
          {data.warnings.map((w: string) => (
            <li key={w} className="flex items-start gap-1.5">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-600" strokeWidth={1.75} /> {w}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function DataQualityPanel() {
  const { data } = useDataQuality();
  if (!data) return <Spinner label="Checking data quality…" />;
  return (
    <Card id="data-quality">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Database className="h-4 w-4 text-black" /> Data quality & provenance
          </CardTitle>
          <CardDescription>{data.provenance}</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
        <table className="w-full text-xs">
          <tbody className="tabular-nums">
            {[
              ["Total executions", num(data.total_executions)],
              ["Successful / failed", `${num(data.successful_executions)} / ${num(data.failures)} (${pct(data.failure_rate)} failure)`],
              ["Configuration parameters", num(data.configuration_parameters)],
              ["Randomized variables", num(data.randomized_variables)],
              ["Telemetry variables", num(data.telemetry_variables)],
              ["Missing values", data.missing_values_note ?? `${num(data.missing_values_total)} cells`],
              ["Duplicate run IDs / rows", `${num(data.duplicate_run_ids)} / ${num(data.duplicate_rows)}`],
              ["Invalid values", Object.keys(data.invalid_values).length ? JSON.stringify(data.invalid_values) : "none detected"],
              ["Log coverage", `${data.log_coverage == null ? "–" : pct(data.log_coverage, 0)} (${data.log_source})`],
              ["Timestamp coverage", `${pct(data.timestamp_coverage, 0)} (${data.timestamp_source}) · ${data.time_span.days} days`],
            ].map(([k, v]) => (
              <tr key={k} className="border-t border-grey-150">
                <td className="py-1.5 pr-3 text-grey-500">{k}</td>
                <td className="py-1.5 text-right font-medium text-black">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="space-y-3 text-xs">
          {Object.keys(data.missing_values ?? {}).length > 0 && (
            <div>
              <div className="mb-1 font-semibold text-grey-800">Missing values by column</div>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(data.missing_values).map(([c, n]) => (
                  <Badge key={c}>{c}: {num(n as number)}</Badge>
                ))}
              </div>
            </div>
          )}
          <div>
            <div className="mb-1 font-semibold text-grey-800">Derived (not in source data)</div>
            {data.derived_fields.length ? (
              <div className="flex flex-wrap gap-1.5">
                {data.derived_fields.map((f: string) => (
                  <Badge key={f} className="border-red-200 bg-red-50 text-red-900">{f}</Badge>
                ))}
              </div>
            ) : (
              <span className="text-grey-500">none</span>
            )}
          </div>
          {data.warnings.length > 0 ? (
            <ul className="space-y-1 text-red-900">
              {data.warnings.map((w: string) => (
                <li key={w} className="flex items-start gap-1.5">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-600" strokeWidth={1.75} /> {w}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-black">No data-quality warnings.</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
