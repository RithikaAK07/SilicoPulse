"use client";
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { Sidebar } from "./sidebar";
import { Header } from "./Header";

const PUBLIC = ["/login"];

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
        <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
      </div>
    );
  }
  return (
    <>
      <Sidebar />
      <main className="min-h-screen px-4 pb-16 pt-3 sm:px-6 lg:ml-64 lg:px-8">
        <div className="mx-auto min-w-0 max-w-[1500px]">
          <Header />
          {children}
        </div>
      </main>
    </>
  );
}
