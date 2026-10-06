import type { LucideIcon } from "lucide-react";
import { AlertTriangle, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { API_URL } from "@/lib/api";
import { stripQuestionMarkers } from "@/lib/utils";
import { Card } from "./ui/card";
import { Skeleton } from "./ui/skeleton";

export function PageHeader({ title, subtitle, badge, actions }: { title: string; subtitle: string; badge?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        {badge && <div className="eyebrow mb-2">{badge}</div>}
        <h1 className="h1 text-black">{title}</h1>
        <p className="mt-2 max-w-3xl text-base text-grey-600">{subtitle}</p>
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="grid gap-6" aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-48 w-full" />
      ))}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-grey-600">
      <Loader2 className="h-4 w-4 animate-spin text-red-600" strokeWidth={1.75} /> {label}
    </span>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-red-600 bg-grad-fail p-5 text-sm text-red-900 shadow-card">
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-red-600" strokeWidth={1.75} />
      <div>
        <div className="font-semibold">Could not reach the analytics API at {API_URL}</div>
        <div className="mt-1 text-red-700">{String((error as Error)?.message ?? error)}</div>
        <div className="mt-2 text-red-700">
          Start the backend: <code>cd backend; .venv\Scripts\uvicorn app.main:app --port 8000</code>
        </div>
      </div>
    </div>
  );
}

export function KpiCard({ label, value, sub, icon: Icon, accent }: { label: string; value: string; sub?: React.ReactNode; icon: LucideIcon; accent: string }) {
  return (
    <Card className="p-5" data-accent={accent}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-2xs font-semibold uppercase tracking-[0.08em] text-grey-600">{label}</span>
        <span className="grid h-8 w-8 place-items-center rounded-lg border border-grey-200 bg-grey-50">
          <Icon className="h-4 w-4 text-grey-500" strokeWidth={1.75} />
        </span>
      </div>
      <div className="mt-3 whitespace-nowrap text-[clamp(30px,2.9vw,44px)] font-semibold leading-[1.08] tracking-[-0.01em] text-black tabular-nums">{value}</div>
      {sub && <div className="mt-1.5 text-xs text-grey-600">{sub}</div>}
    </Card>
  );
}

/** LLMs sometimes emit inline LaTeX ($n=517$, $\le 8$); render it as plain text instead of raw dollar signs. */
function stripInlineMath(md: string) {
  return md
    .replace(/\$([^$\n]{1,60})\$/g, (_, inner: string) => inner)
    .replace(/\\(le|leq)\b/g, "≤")
    .replace(/\\(ge|geq)\b/g, "≥")
    .replace(/\\times\b/g, "×")
    .replace(/\\approx\b/g, "≈")
    .replace(/\\%/g, "%");
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-hub">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{stripQuestionMarkers(stripInlineMath(children))}</ReactMarkdown>
    </div>
  );
}

export function SourceBadge({ source }: { source?: string }) {
  if (!source) return null;
  const live = !source.startsWith("local");
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-2xs font-semibold ${live ? "border-grey-200 bg-white text-black" : "border-red-200 bg-red-50 text-red-900"}`}
      title={source}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-red-600" : "bg-grey-500"}`} />
      {live ? `Gemini · ${source}` : "Local heuristic AI"}
    </span>
  );
}

/** Shared Recharts tooltip: black gradient surface, white text, a ringed swatch carries identity. */
export function ChartTooltip({ active, payload, label, fmt, labelFmt }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tip">
      {label !== undefined && label !== "" && (
        <div className="mb-1 font-semibold text-white">{labelFmt ? labelFmt(label, payload) : label}</div>
      )}
      {payload.map((p: any, i: number) => (
        <div key={i} className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full ring-1 ring-white/70" style={{ background: p.color ?? p.payload?.fill }} />
          <span className="tip-muted">{p.name}</span>
          <span className="ml-auto pl-3 font-semibold tabular-nums text-white">{fmt ? fmt(p.value, p.name) : p.value}</span>
        </div>
      ))}
    </div>
  );
}

export const axisProps = { stroke: "#D4D4DA", tick: { fill: "#52525B", fontSize: 12, fontFamily: "Alegreya, Georgia, serif" }, tickLine: false } as const;
