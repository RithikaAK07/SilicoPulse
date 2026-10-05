"use client";
import Link from "next/link";
import { Briefcase, Cpu, Database, FileSpreadsheet, LogOut, ShieldCheck, Zap } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { num } from "@/lib/utils";

const ROLE_ICON = { admin: ShieldCheck, engineer: Cpu, executive: Briefcase } as const;

export function Header() {
  const { user, dataset, logout } = useAuth();
  if (!user) return null;
  const RoleIcon = ROLE_ICON[user.role];
  const uploaded = dataset?.source === "uploaded";
  const DatasetIcon = uploaded ? FileSpreadsheet : Database;
  return (
    <header className="mb-8 flex flex-wrap items-center justify-end gap-2 border-b border-grey-200 pb-4 pl-12 sm:pl-0">
      <Link href="/dashboard" className="mr-auto flex items-center gap-2.5" aria-label="SilicoPulse home">
        <span className="grid h-8 w-8 place-items-center rounded-[9px] bg-grad-black">
          <Zap className="h-4 w-4 text-white" strokeWidth={1.75} />
        </span>
        <span className="text-lg font-semibold text-black">SilicoPulse</span>
        <span className="hidden text-xs text-grey-500 2xl:inline">· AI-Powered Silicon Validation &amp; Configuration Intelligence Platform</span>
      </Link>
      {dataset && (
        <Link
          href="/upload"
          title={uploaded ? `${dataset.filename} · ${num(dataset.rows)} rows` : `Synthetic benchmark · ${num(dataset.rows)} rows`}
          className="inline-flex max-w-[16rem] items-center gap-1.5 rounded-full border border-grey-300 bg-white px-3 py-1 text-xs font-semibold text-black hover:border-black"
        >
          <DatasetIcon className="h-3.5 w-3.5 shrink-0 text-grey-500" strokeWidth={1.75} />
          <span className="truncate">
            {dataset.label}
            {uploaded && dataset.filename ? ` · ${dataset.filename}` : ""}
          </span>
        </Link>
      )}
      <span className="inline-flex items-center gap-1.5 rounded-full bg-grad-black px-3 py-1 text-xs font-semibold text-white">
        <RoleIcon className="h-3.5 w-3.5 text-red-400" strokeWidth={1.75} />
        {user.role_title}
      </span>
      <span className="hidden text-xs text-grey-600 md:inline">{user.email}</span>
      <button
        onClick={logout}
        className="inline-flex h-8 items-center gap-1.5 rounded-[10px] px-3 text-xs font-semibold text-grey-600 hover:bg-red-50 hover:text-red-700"
      >
        <LogOut className="h-4 w-4" strokeWidth={1.75} /> Logout
      </button>
    </header>
  );
}
