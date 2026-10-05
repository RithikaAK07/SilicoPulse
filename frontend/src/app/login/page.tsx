"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, ArrowRight, Briefcase, Cpu, Loader2, Lock, Mail, ShieldCheck } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

const DEMO = [
  {
    label: "Login as Validation Lead",
    email: "admin@sandisk.com",
    password: "admin123",
    icon: ShieldCheck,
    accent: "from-indigo-500 to-violet-600",
    blurb: "Full access · upload data · generate datasets",
  },
  {
    label: "Login as VLSI Engineer",
    email: "engineer@sandisk.com",
    password: "eng123",
    icon: Cpu,
    accent: "from-sky-500 to-blue-600",
    blurb: "Analyze, diff & upload execution logs",
  },
  {
    label: "Login as Executive",
    email: "executive@sandisk.com",
    password: "exec123",
    icon: Briefcase,
    accent: "from-amber-500 to-orange-600",
    blurb: "Read-only dashboards & AI summaries",
  },
];

export default function LoginPage() {
  const { login, user, ready } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ready && user) router.replace("/dashboard");
  }, [ready, user, router]);

  async function submit(e?: string, p?: string) {
    const em = e ?? email;
    const pw = p ?? password;
    setError(null);
    setBusy(em || "form");
    try {
      await login(em, pw);
      router.replace("/dashboard");
    } catch (err) {
      const msg = (err as Error).message;
      setError(msg.includes("fetch") ? "Cannot reach the analytics API on port 8000. Is the backend running?" : msg);
      setBusy(null);
    }
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-slate-950 text-slate-100">
      <div className="pointer-events-none absolute -left-40 -top-40 h-[32rem] w-[32rem] rounded-full bg-sky-600/20 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-40 -right-40 h-[32rem] w-[32rem] rounded-full bg-indigo-600/20 blur-3xl" />
      <div className="relative mx-auto grid min-h-screen max-w-6xl items-center gap-10 px-4 py-10 sm:px-6 lg:grid-cols-[1.1fr_1fr]">
        <section className="hidden lg:block">
          <div className="mb-6 flex items-center gap-3">
            <div className="grid h-11 w-11 place-items-center rounded-xl bg-gradient-to-br from-sky-500 to-indigo-600">
              <Cpu className="h-6 w-6 text-white" />
            </div>
            <div>
              <div className="text-xl font-semibold leading-tight">SilicoPulse</div>
              <div className="text-xs leading-tight text-sky-400">AI-Powered Silicon Validation &amp; Configuration Intelligence Platform</div>
            </div>
          </div>
          <h1 className="text-4xl font-semibold leading-tight tracking-tight">
            Turn 10,000 test executions into <span className="text-sky-400">the next right configuration.</span>
          </h1>
          <p className="mt-4 max-w-lg text-slate-400">
            Feature importance, Pareto discovery, seed determinism, root-cause fingerprints, failure-risk prediction and AI recommendations, grounded in your own execution logs.
          </p>
          <ul className="mt-8 grid max-w-lg grid-cols-2 gap-3 text-sm text-slate-300">
            {["LightGBM + RF risk models", "DuckDB OLAP analytics", "Gemini AI copilot", "CSV log ingestion"].map((f) => (
              <li key={f} className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">{f}</li>
            ))}
          </ul>
        </section>

        <section className="w-full rounded-2xl border border-slate-800 bg-slate-900/80 p-6 shadow-2xl backdrop-blur sm:p-8">
          <div className="mb-4 flex items-center gap-2 lg:hidden">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-sky-500 to-indigo-600">
              <Cpu className="h-4 w-4 text-white" />
            </div>
            <span className="font-semibold">SilicoPulse</span>
          </div>
          <h2 className="text-xl font-semibold">Welcome to SilicoPulse</h2>
          <p className="mt-1 text-sm text-sky-400">SanDisk Hardware Validation &amp; Log Intelligence</p>
          <p className="mt-3 text-sm text-slate-400">Use a one-click demo role or your credentials.</p>

          <div className="mt-6 grid gap-2.5">
            {DEMO.map((d) => (
              <button
                key={d.email}
                disabled={!!busy}
                onClick={() => {
                  setEmail(d.email);
                  setPassword(d.password);
                  submit(d.email, d.password);
                }}
                className="group flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-950/60 p-3 text-left transition-colors hover:border-sky-700 hover:bg-slate-900 disabled:opacity-60"
              >
                <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-gradient-to-br ${d.accent}`}>
                  {busy === d.email ? <Loader2 className="h-5 w-5 animate-spin text-white" /> : <d.icon className="h-5 w-5 text-white" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-slate-100">{d.label}</span>
                  <span className="block truncate text-xs text-slate-400">{d.blurb}</span>
                </span>
                <ArrowRight className="h-4 w-4 text-slate-500 transition-transform group-hover:translate-x-0.5 group-hover:text-sky-400" />
              </button>
            ))}
          </div>

          <div className="my-6 flex items-center gap-3 text-xs text-slate-500">
            <span className="h-px flex-1 bg-slate-800" /> or sign in with email <span className="h-px flex-1 bg-slate-800" />
          </div>

          <form
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <label className="relative block">
              <span className="sr-only">Email</span>
              <Mail className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-500" />
              <input
                type="email"
                required
                autoComplete="username"
                placeholder="you@sandisk.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="h-10 w-full rounded-lg border border-slate-700 bg-slate-950 pl-9 pr-3 text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-sky-500"
              />
            </label>
            <label className="relative block">
              <span className="sr-only">Password</span>
              <Lock className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-500" />
              <input
                type="password"
                required
                autoComplete="current-password"
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="h-10 w-full rounded-lg border border-slate-700 bg-slate-950 pl-9 pr-3 text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-sky-500"
              />
            </label>
            {error && (
              <div className="flex items-start gap-2 rounded-lg border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-200">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
              </div>
            )}
            <button
              type="submit"
              disabled={!!busy}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-sky-600 text-sm font-medium text-white transition-colors hover:bg-sky-500 disabled:opacity-60"
            >
              {busy === email || busy === "form" ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Sign in
            </button>
          </form>
          <p className="mt-6 text-center text-[11px] text-slate-500">Demo environment · mock identities · JWT session (8h)</p>
        </section>
      </div>
    </div>
  );
}
