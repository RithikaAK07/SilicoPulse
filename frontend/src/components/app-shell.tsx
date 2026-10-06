"use client";
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useIsFetching, useIsMutating } from "@tanstack/react-query";
import Link from "next/link";
import { Activity, Loader2 } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { Sidebar } from "./sidebar";
import { Header } from "./Header";

const PUBLIC = ["/login"];
/** Views computed by the execution pipeline (PASS/FAIL, configuration, seeds, failure signatures, models). */
const EXECUTION_VIEWS = ["/dashboard", "/discovery", "/randomization", "/root-cause", "/predictive", "/insights", "/copilot"];

/** Shown instead of an execution view when the active dataset is telemetry (nothing is computed on it). */
function NotAvailableForTelemetry({ name, type }: { name?: string; type?: string }) {
  return (
    <section className="rounded-xl border border-grey-200 bg-white p-8 shadow-card" aria-label="Not available for this dataset">
      <div className="eyebrow mb-2">Not available for this dataset</div>
      <h2 className="text-xl font-semibold text-black">This view needs an execution dataset</h2>
      <p className="mt-2 max-w-2xl text-sm text-grey-600">
        The active dataset <span className="font-mono text-grey-800">{name}</span> is {type === "time_series_telemetry" ? "time-series" : "industrial"} telemetry.
        This view analyses execution logs (PASS/FAIL outcomes, configuration parameters, seeds and failure signatures), which telemetry does not contain,
        so nothing is computed or invented here.
      </p>
      <div className="mt-5 flex flex-wrap gap-2">
        <Link href="/telemetry" className="inline-flex h-10 items-center gap-2 rounded-[10px] bg-grad-red px-4 text-sm font-semibold text-white shadow-cta">
          <Activity className="h-4 w-4" /> Open Telemetry Health
        </Link>
        <Link href="/generator" className="inline-flex h-10 items-center rounded-[10px] border border-grey-300 bg-white px-4 text-sm font-semibold text-black hover:border-grey-400">
          Change active dataset
        </Link>
      </div>
    </section>
  );
}

/** Thin red page-level progress bar while any query/mutation is in flight. */
function ProgressBar() {
  const busy = useIsFetching() + useIsMutating() > 0;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-0 z-[60] h-0.5 overflow-hidden" aria-hidden>
      {busy && <div className="sp-progress h-full w-1/3 animate-progress" />}
    </div>
  );
}

/** Chooses the chrome for a route: bare page for /login, sidebar + header for the authenticated app. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, ready, dataset } = useAuth();
  const path = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC.includes(path);

  useEffect(() => {
    if (ready && !user && !isPublic) router.replace("/login");
  }, [ready, user, isPublic, router]);

  if (isPublic) return <>{children}</>;
  if (!ready || !user) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Loader2 className="h-6 w-6 animate-spin text-grey-500" strokeWidth={1.75} />
      </div>
    );
  }
  return (
    <div data-theme="silicopulse" className="contents">
      <ProgressBar />
      <Sidebar />
      <main className="min-h-screen px-4 pb-16 pt-3 sm:ml-[72px] sm:px-6 sm:pt-5 lg:ml-64 lg:px-8">
        <div className="mx-auto min-w-0 max-w-[1440px]">
          <Header />
          {dataset?.active === "uploaded_telemetry" && EXECUTION_VIEWS.some((v) => path.startsWith(v)) ? (
            <NotAvailableForTelemetry name={dataset.active_name} type={dataset.active_type} />
          ) : (
            children
          )}
        </div>
      </main>
    </div>
  );
}
