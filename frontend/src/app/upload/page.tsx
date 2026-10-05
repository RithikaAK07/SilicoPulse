"use client";
import { PageHeader } from "@/components/common";
import { UploadTab } from "@/components/UploadTab";

export default function UploadPage() {
  return (
    <>
      <PageHeader
        badge="Enterprise · Data ingestion"
        title="CSV Data Upload"
        subtitle="Ingest real execution logs. DuckDB is re-indexed and every model (feature importance, risk prediction, throughput) is retrained, so all Q1–Q8 views and the AI Copilot switch to your data."
      />
      <UploadTab />
    </>
  );
}
