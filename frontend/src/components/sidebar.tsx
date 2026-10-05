"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Bot, BrainCircuit, Cpu, Database, Dices, LayoutDashboard, Menu, SearchCode, ShieldCheck, Sparkles, Upload, X } from "lucide-react";
import { useMeta } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { cn, num } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Executive Overview", icon: LayoutDashboard, q: "Summary" },
  { href: "/discovery", label: "Config Discovery", icon: BrainCircuit, q: "Q1 · Q2" },
  { href: "/randomization", label: "Randomization Engine", icon: Dices, q: "Q3 · Q4" },
  { href: "/root-cause", label: "Root Cause & Logs", icon: SearchCode, q: "Q5 · Q6" },
  { href: "/predictive", label: "Predict & Prescribe", icon: Sparkles, q: "Q7 · Q8" },
  { href: "/insights", label: "Evidence & Guardrails", icon: ShieldCheck, q: "Trust" },
  { href: "/copilot", label: "AI Copilot", icon: Bot, q: "NL query" },
  { href: "/upload", label: "CSV Data Upload", icon: Upload, q: "Ingest" },
  { href: "/generator", label: "Dataset Generator", icon: Database, q: "Synthetic" },
];

/** Full sidebar >= 1024px, 72px icon rail 640-1023px (logo expands it), off-canvas drawer < 640px. */
export function Sidebar() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const { data: meta } = useMeta();
  const { user, dataset } = useAuth();
  const text = open ? "" : "hidden lg:inline"; // label visibility in rail mode
  const initials = user?.name.split(" ").map((p) => p[0]).slice(0, 2).join("") ?? "";
  return (
    <>
      <button
        className="fixed left-3 top-3 z-50 grid h-10 w-10 place-items-center rounded-[10px] border border-grey-300 bg-white text-black shadow-card sm:hidden"
        onClick={() => setOpen(!open)}
        aria-label="Toggle navigation"
      >
        {open ? <X className="h-5 w-5" strokeWidth={1.75} /> : <Menu className="h-5 w-5" strokeWidth={1.75} />}
      </button>
      {open && <div className="fixed inset-0 z-30 bg-black/20 lg:hidden" onClick={() => setOpen(false)} aria-hidden />}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex flex-col overflow-y-auto border-r border-grey-200 bg-grad-sidebar transition-[width,transform] duration-150",
          open ? "w-64 translate-x-0 shadow-card-hover" : "w-64 -translate-x-full sm:w-[72px] sm:translate-x-0 lg:w-64",
        )}
      >
        <div className={cn("flex items-center gap-3 py-5", open ? "px-5" : "px-5 sm:justify-center sm:px-0 lg:justify-start lg:px-5")}>
          <button
            onClick={() => setOpen(!open)}
            className="relative grid h-10 w-10 shrink-0 place-items-center rounded-[10px] bg-grad-black shadow-card lg:pointer-events-none"
            aria-label="Expand navigation"
            tabIndex={-1}
          >
            <Cpu className="h-5 w-5 text-white" strokeWidth={1.75} />
            <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-red-500" aria-hidden />
          </button>
          <div className={cn("min-w-0", text)}>
            <span className="inline-flex items-center rounded-full bg-grad-black px-2.5 py-0.5 text-xs font-semibold tracking-wide text-white">
              SilicoPulse AI
            </span>
            <div className="mt-1 text-2xs leading-tight text-grey-500">Silicon Validation &amp; Config Intelligence</div>
          </div>
        </div>
        <nav className={cn("flex-1 space-y-1", open ? "px-3" : "px-3 sm:px-2.5 lg:px-3")}>
          {NAV.map(({ href, label, icon: Icon, q }) => {
            const active = path.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                title={label}
                onClick={() => setOpen(false)}
                className={cn(
                  "group relative flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-sm",
                  !open && "sm:justify-center lg:justify-start",
                  active ? "bg-grad-black text-white shadow-card" : "text-grey-600 hover:bg-black/[0.04] hover:text-black",
                )}
              >
                {active && <span className="absolute inset-y-2 left-0 w-[3px] rounded-r bg-red-600" aria-hidden />}
                <Icon className={cn("h-4 w-4 shrink-0", active ? "text-red-400" : "text-grey-500 group-hover:text-black")} strokeWidth={1.75} />
                <span className={cn("flex-1", text)}>{label}</span>
                <span className={cn("text-2xs", active ? "text-grey-300" : "text-grey-500", text)}>{q}</span>
              </Link>
            );
          })}
        </nav>
        {user && (
          <div className={cn("mx-3 mb-2 flex items-center gap-2.5 rounded-[10px] px-2 py-2", !open && "sm:justify-center lg:justify-start")}>
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-grad-black text-xs font-semibold text-white" title={`${user.name} · ${user.role_title}`}>
              {initials}
            </span>
            <div className={cn("min-w-0 text-xs", text)}>
              <div className="truncate font-semibold text-black">{user.name}</div>
              <div className="truncate text-grey-600">{user.role_title}</div>
            </div>
          </div>
        )}
        <div className={cn("m-3 mt-0 rounded-[10px] border border-grey-200 bg-grey-50 p-3 text-xs text-grey-600", open ? "" : "hidden lg:block")}>
          {meta ? (
            <div className="space-y-1">
              <div className="mb-1.5 flex items-center justify-between border-b border-grey-200 pb-1.5">
                <span>Dataset</span>
                <span className="font-semibold text-black">{dataset?.source === "uploaded" ? "Uploaded CSV" : "Benchmark"}</span>
              </div>
              <Row k="Executions" v={num(meta.n_runs)} />
              <Row k="Config params" v={String(meta.n_config_params)} />
              <Row k="Random vars" v={String(meta.n_random_vars)} />
              <Row k="Model AUC" v={meta.model.auc.toFixed(3)} />
              <div className="flex justify-between">
                <span>GenAI</span>
                <span className={meta.ai.gemini_configured ? "font-semibold text-black" : "font-semibold text-red-700"}>
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
      <span className="tabular-nums text-black">{v}</span>
    </div>
  );
}
