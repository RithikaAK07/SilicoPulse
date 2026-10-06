"use client";
import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Upload } from "lucide-react";
import { api, post } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { ErrorState, Loading, PageHeader } from "@/components/common";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { TelemetryResult } from "@/components/UploadPreprocess";

/** Telemetry pipeline dashboard: the ingested telemetry dataset (persisted server-side), never mock data. */
export default function TelemetryPage() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [clearing, setClearing] = useState(false);
  const q = useQuery({ queryKey: ["telemetry-status"], queryFn: () => api<any>("/api/telemetry/status") });

  async function clear() {
    setClearing(true);
    try {
      await post("/api/telemetry/reset", {});
      await qc.invalidateQueries({ queryKey: ["telemetry-status"] });
    } finally {
      setClearing(false);
    }
  }

  return (
    <>
      <PageHeader
        badge="Telemetry pipeline · Machine health"
        title="Telemetry Health"
        subtitle="Channel statistics, trends, anomaly windows, channel health and correlations computed from the ingested telemetry dataset. Telemetry needs no PASS/FAIL outcome and is kept separate from the execution dataset used by the other views."
      />
      {q.isLoading ? (
        <Loading rows={4} />
      ) : q.isError ? (
        <ErrorState error={q.error} />
      ) : q.data?.active ? (
        <TelemetryResult status={q.data} onClear={clear} canClear={can("upload")} clearing={clearing} />
      ) : (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-14 text-center">
            <Activity className="h-9 w-9 text-grey-400" strokeWidth={1.5} />
            <div className="text-base font-semibold text-black">No telemetry dataset ingested yet</div>
            <p className="max-w-md text-sm text-grey-600">
              Upload a telemetry file (CSV, TXT, LOG, JSON, Excel or ZIP with a timestamp and numeric measurement channels) and choose “Ingest as telemetry”.
            </p>
            <Link href="/upload">
              <Button variant="primary"><Upload className="h-4 w-4" /> Go to data upload</Button>
            </Link>
          </CardContent>
        </Card>
      )}
    </>
  );
}
