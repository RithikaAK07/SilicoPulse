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
    tile: "bg-white text-black",
    blurb: "Full access · upload data · generate datasets",
  },
  {
    label: "Login as VLSI Engineer",
    email: "engineer@sandisk.com",
    password: "eng123",
    icon: Cpu,
    tile: "bg-grad-red text-white",
    blurb: "Analyze, diff & upload execution logs",
  },
  {
    label: "Login as Executive",
    email: "executive@sandisk.com",
    password: "exec123",
    icon: Briefcase,
    tile: "bg-gradient-to-br from-grey-500 to-grey-600 text-white",
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
    <div className="relative min-h-screen overflow-hidden bg-grad-login text-white">
      <div className="relative mx-auto grid min-h-screen max-w-6xl items-center gap-12 px-4 py-10 sm:px-6 lg:grid-cols-[1.1fr_1fr]">
        <section className="hidden lg:block">
          <div className="mb-8 flex items-center gap-3">
            <div className="relative grid h-12 w-12 place-items-center rounded-[12px] border border-white/10 bg-grad-black">
              <Cpu className="h-6 w-6 text-white" strokeWidth={1.75} />
              <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-red-500" aria-hidden />
            </div>
            <div>
              <div className="text-2xl font-semibold leading-tight">SilicoPulse</div>
              <div className="text-xs leading-tight text-red-400">AI-Powered Silicon Validation &amp; Configuration Intelligence Platform</div>
            </div>
          </div>
          <h1 className="text-5xl font-semibold tracking-[-0.01em] text-white">
            Turn 10,000 test executions into{" "}
            <span className="bg-gradient-to-r from-red-400 to-red-600 bg-clip-text text-transparent">the next right configuration.</span>
          </h1>
          <p className="mt-5 max-w-lg text-base text-white/70">
            Feature importance, Pareto discovery, seed determinism, root-cause fingerprints, failure-risk prediction and AI recommendations, grounded in your own execution logs.
          </p>
          <ul className="mt-8 grid max-w-lg grid-cols-2 gap-3 text-sm text-white">
            {["LightGBM + RF risk models", "DuckDB OLAP analytics", "Gemini AI copilot", "CSV log ingestion"].map((f) => (
              <li key={f} className="rounded-[10px] border border-white/[0.12] bg-white/[0.06] px-3 py-2">{f}</li>
            ))}
          </ul>
        </section>

        <section className="w-full rounded-[18px] border border-white/[0.12] bg-white/[0.04] p-6 shadow-2xl backdrop-blur-md sm:p-8">
          <div className="mb-4 flex items-center gap-2 lg:hidden">
            <div className="grid h-9 w-9 place-items-center rounded-[10px] border border-white/10 bg-grad-black">
              <Cpu className="h-4 w-4 text-white" strokeWidth={1.75} />
            </div>
            <span className="text-lg font-semibold">SilicoPulse</span>
          </div>
          <h2 className="text-3xl font-semibold text-white">Welcome to SilicoPulse</h2>
          <p className="mt-1 text-sm text-red-400">SanDisk Hardware Validation &amp; Log Intelligence</p>
          <p className="mt-3 text-sm text-white/70">Use a one-click demo role or your credentials.</p>

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
                className="group flex items-center gap-3 rounded-xl border border-white/[0.12] bg-white/[0.04] p-3 text-left hover:border-red-600 hover:bg-white/[0.07] disabled:opacity-60"
              >
                <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-[10px] ${d.tile}`}>
                  {busy === d.email ? <Loader2 className="h-5 w-5 animate-spin" strokeWidth={1.75} /> : <d.icon className="h-5 w-5" strokeWidth={1.75} />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-base font-semibold text-white">{d.label}</span>
                  <span className="block truncate text-xs text-white/65">{d.blurb}</span>
                </span>
                <ArrowRight className="h-4 w-4 text-white/50 transition-transform group-hover:translate-x-0.5 group-hover:text-red-400" strokeWidth={1.75} />
              </button>
            ))}
          </div>

          <div className="my-6 flex items-center gap-3 text-xs text-white/60">
            <span className="h-px flex-1 bg-white/[0.12]" /> or sign in with email <span className="h-px flex-1 bg-white/[0.12]" />
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
              <Mail className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-white/55" strokeWidth={1.75} />
              <input
                type="email"
                required
                autoComplete="username"
                placeholder="you@sandisk.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="h-10 w-full rounded-[10px] border border-white/[0.16] bg-white/[0.06] pl-9 pr-3 text-sm text-white placeholder:text-white/50 hover:border-white/30 focus:border-red-500 focus:shadow-focus focus:outline-none"
              />
            </label>
            <label className="relative block">
              <span className="sr-only">Password</span>
              <Lock className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-white/55" strokeWidth={1.75} />
              <input
                type="password"
                required
                autoComplete="current-password"
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="h-10 w-full rounded-[10px] border border-white/[0.16] bg-white/[0.06] pl-9 pr-3 text-sm text-white placeholder:text-white/50 hover:border-white/30 focus:border-red-500 focus:shadow-focus focus:outline-none"
              />
            </label>
            {error && (
              <div className="flex items-start gap-2 rounded-[10px] border border-red-500/50 bg-red-600/15 px-3 py-2 text-sm text-red-100">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" strokeWidth={1.75} /> {error}
              </div>
            )}
            <button
              type="submit"
              disabled={!!busy}
              className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] bg-grad-red text-sm font-semibold text-white shadow-cta hover:-translate-y-px hover:brightness-110 active:translate-y-0 active:brightness-100 disabled:opacity-60"
            >
              {busy === email || busy === "form" ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Sign in
            </button>
          </form>
          <p className="mt-6 text-center text-2xs text-white/60">Demo environment · mock identities · JWT session (8h)</p>
        </section>
      </div>
    </div>
  );
}
