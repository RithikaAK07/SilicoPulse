"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Bot, BrainCircuit, Cpu, Database, Dices, LayoutDashboard, Menu, SearchCode, Sparkles, Upload, X } from "lucide-react";
import { useMeta } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { cn, num } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Executive Overview", icon: LayoutDashboard, q: "Summary" },
  { href: "/discovery", label: "Config Discovery", icon: BrainCircuit, q: "Q1 · Q2" },
  { href: "/randomization", label: "Randomization Engine", icon: Dices, q: "Q3 · Q4" },
  { href: "/root-cause", label: "Root Cause & Logs", icon: SearchCode, q: "Q5 · Q6" },
  { href: "/predictive", label: "Predict & Prescribe", icon: Sparkles, q: "Q7 · Q8" },
  { href: "/copilot", label: "AI Copilot", icon: Bot, q: "NL query" },
  { href: "/upload", label: "CSV Data Upload", icon: Upload, q: "Ingest" },
  { href: "/generator", label: "Dataset Generator", icon: Database, q: "Synthetic" },
];

export function Sidebar() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const { data: meta } = useMeta();
  const { user, dataset } = useAuth();
  return (
    <>
      <button
        className="fixed left-3 top-3 z-50 rounded-lg border border-slate-300 bg-white p-2 lg:hidden"
        onClick={() => setOpen(!open)}
        aria-label="Toggle navigation"
      >
        {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
      </button>
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-slate-200 bg-white backdrop-blur transition-transform lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex items-center gap-3 px-5 py-5">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-sky-500 to-indigo-600">
            <Cpu className="h-5 w-5 text-white" />
          </div>
          <div className="min-w-0">
            <span className="inline-flex items-center gap-1 rounded-md bg-gradient-to-r from-sky-500 to-indigo-600 px-2 py-0.5 text-xs font-semibold tracking-wide text-white">
              SilicoPulse AI
            </span>
            <div className="mt-1 text-[10px] leading-tight text-slate-500">Silicon Validation &amp; Config Intelligence</div>
          </div>
        </div>
        <nav className="flex-1 space-y-1 px-3">
          {NAV.map(({ href, label, icon: Icon, q }) => {
            const active = path.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                onClick={() => setOpen(false)}
                className={cn(
                  "group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors",
                  active ? "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-500/30" : "text-slate-500 hover:bg-slate-100 hover:text-slate-900",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="flex-1">{label}</span>
                <span className="text-[10px] text-slate-500">{q}</span>
              </Link>
            );
          })}
        </nav>
        {user && (
          <div className="mx-3 mb-2 rounded-lg px-3 py-2 text-xs">
            <div className="font-medium text-slate-800">{user.name}</div>
            <div className="text-slate-500">{user.role_title}</div>
          </div>
        )}
        <div className="m-3 mt-0 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-500">
          {meta ? (
            <div className="space-y-1">
              <div className="mb-1.5 flex items-center justify-between border-b border-slate-200 pb-1.5">
                <span>Dataset</span>
                <span className={dataset?.source === "uploaded" ? "font-medium text-emerald-700" : "text-slate-800"}>
                  {dataset?.source === "uploaded" ? "Uploaded CSV" : "Benchmark"}
                </span>
              </div>
              <Row k="Executions" v={num(meta.n_runs)} />
              <Row k="Config params" v={String(meta.n_config_params)} />
              <Row k="Random vars" v={String(meta.n_random_vars)} />
              <Row k="Model AUC" v={meta.model.auc.toFixed(3)} />
              <div className="flex justify-between">
                <span>GenAI</span>
                <span className={meta.ai.gemini_configured ? "text-emerald-600" : "text-amber-600"}>
                  {meta.ai.gemini_configured ? "Gemini" : "Local fallback"}
                </span>
              </div>
            </div>
          ) : (
            <span>Connecting to analytics API…</span>
          )}
        </div>
      </aside>
    </>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between">
      <span>{k}</span>
      <span className="text-slate-800">{v}</span>
    </div>
  );
}
