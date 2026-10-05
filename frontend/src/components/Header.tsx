"use client";
import Link from "next/link";
import { Briefcase, Cpu, Database, FileSpreadsheet, LogOut, ShieldCheck, Zap } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { Button } from "./ui/button";
import { cn, num } from "@/lib/utils";

const ROLE_STYLE = {
  admin: { icon: ShieldCheck, cls: "border-indigo-200 bg-indigo-50 text-indigo-800" },
  engineer: { icon: Cpu, cls: "border-sky-200 bg-sky-50 text-sky-800" },
  executive: { icon: Briefcase, cls: "border-amber-200 bg-amber-50 text-amber-800" },
} as const;

export function Header() {
  const { user, dataset, logout } = useAuth();
  if (!user) return null;
  const role = ROLE_STYLE[user.role];
  const uploaded = dataset?.source === "uploaded";
  const DatasetIcon = uploaded ? FileSpreadsheet : Database;
  return (
    <header className="mb-5 flex flex-wrap items-center justify-end gap-2 border-b border-slate-200 pb-3 pl-12 lg:pl-0">
      <Link href="/dashboard" className="mr-auto flex items-center gap-2" aria-label="SilicoPulse home">
        <span className="grid h-7 w-7 place-items-center rounded-md bg-gradient-to-br from-sky-500 to-indigo-600">
          <Zap className="h-4 w-4 text-white" />
        </span>
        <span className="text-sm font-semibold tracking-tight text-slate-900">SilicoPulse</span>
        <span className="hidden text-xs text-slate-500 2xl:inline">· AI-Powered Silicon Validation &amp; Configuration Intelligence Platform</span>
      </Link>
      {dataset && (
        <Link
          href="/upload"
          title={uploaded ? `${dataset.filename} · ${num(dataset.rows)} rows` : `Synthetic benchmark · ${num(dataset.rows)} rows`}
          className={cn(
            "inline-flex max-w-xs items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors",
            uploaded ? "border-emerald-200 bg-emerald-50 text-emerald-800 hover:bg-emerald-100" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50",
          )}
        >
          <DatasetIcon className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">
            {dataset.label}
            {uploaded && dataset.filename ? ` · ${dataset.filename}` : ""}
          </span>
        </Link>
      )}
      <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium", role.cls)}>
        <role.icon className="h-3.5 w-3.5" />
        {user.role_title}
      </span>
      <span className="hidden text-xs text-slate-500 md:inline">{user.email}</span>
      <Button variant="ghost" size="sm" onClick={logout}>
        <LogOut className="h-4 w-4" /> Logout
      </Button>
    </header>
  );
}
