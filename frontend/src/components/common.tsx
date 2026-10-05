import type { LucideIcon } from "lucide-react";
import { AlertTriangle, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { API_URL } from "@/lib/api";
import { Card } from "./ui/card";
import { Skeleton } from "./ui/skeleton";

export function PageHeader({ title, subtitle, badge, actions }: { title: string; subtitle: string; badge?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        {badge && <div className="mb-1 text-xs font-medium uppercase tracking-wider text-sky-600">{badge}</div>}
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-500">{subtitle}</p>
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="grid gap-4">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-48 w-full" />
      ))}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-slate-500">
      <Loader2 className="h-4 w-4 animate-spin" /> {label}
    </span>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-800">
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
      <div>
        <div className="font-medium">Could not reach the analytics API at {API_URL}</div>
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
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-slate-500">{label}</span>
        <span className="grid h-7 w-7 place-items-center rounded-md" style={{ background: `${accent}22` }}>
          <Icon className="h-4 w-4" style={{ color: accent }} />
        </span>
      </div>
      <div className="mt-2 text-2xl font-semibold tracking-tight text-slate-900">{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
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
    <div className="prose-hub text-sm leading-relaxed text-slate-800">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{stripInlineMath(children)}</ReactMarkdown>
    </div>
  );
}

export function SourceBadge({ source }: { source?: string }) {
  if (!source) return null;
  const live = !source.startsWith("local");
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[11px] ${live ? "bg-emerald-500/10 text-emerald-700" : "bg-amber-500/10 text-amber-700"}`}
      title={source}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-emerald-400" : "bg-amber-400"}`} />
      {live ? `Gemini · ${source}` : "Local heuristic AI"}
    </span>
  );
}

/** Shared Recharts tooltip: dark surface, values in text ink, a color swatch carries identity. */
export function ChartTooltip({ active, payload, label, fmt, labelFmt }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs shadow-xl">
      {label !== undefined && label !== "" && (
        <div className="mb-1 font-medium text-slate-800">{labelFmt ? labelFmt(label, payload) : label}</div>
      )}
      {payload.map((p: any, i: number) => (
        <div key={i} className="flex items-center gap-2 text-slate-700">
          <span className="h-2 w-2 rounded-full" style={{ background: p.color ?? p.payload?.fill }} />
          <span className="text-slate-500">{p.name}</span>
          <span className="ml-auto pl-3 font-medium tabular-nums text-slate-900">{fmt ? fmt(p.value, p.name) : p.value}</span>
        </div>
      ))}
    </div>
  );
}

export const axisProps = { stroke: "#cbd5e1", tick: { fill: "#64748b", fontSize: 11 }, tickLine: false } as const;
