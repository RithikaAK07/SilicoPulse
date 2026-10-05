"use client";
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useIsFetching, useIsMutating } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { Sidebar } from "./sidebar";
import { Header } from "./Header";

const PUBLIC = ["/login"];

/** Thin red page-level progress bar while any query/mutation is in flight. */
function ProgressBar() {
  const busy = useIsFetching() + useIsMutating() > 0;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-0 z-[60] h-0.5 overflow-hidden" aria-hidden>
      {busy && <div className="h-full w-1/3 animate-progress bg-red-600" />}
    </div>
  );
}

/** Chooses the chrome for a route: bare page for /login, sidebar + header for the authenticated app. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth();
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
    <>
      <ProgressBar />
      <Sidebar />
      <main className="min-h-screen px-4 pb-16 pt-3 sm:ml-[72px] sm:px-6 sm:pt-5 lg:ml-64 lg:px-8">
        <div className="mx-auto min-w-0 max-w-[1440px]">
          <Header />
          {children}
        </div>
      </main>
    </>
  );
}
