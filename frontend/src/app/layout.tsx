import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/components/providers";
import { AppShell } from "@/components/app-shell";
import { AuthProvider } from "@/context/AuthContext";

export const metadata: Metadata = {
  title: "SilicoPulse | AI-Powered Silicon Configuration Intelligence",
  description: "SilicoPulse: AI-Powered Silicon Validation & Configuration Intelligence Platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-grey-50 font-sans text-black antialiased">
        <Providers>
          <AuthProvider>
            <AppShell>{children}</AppShell>
          </AuthProvider>
        </Providers>
      </body>
    </html>
  );
}
