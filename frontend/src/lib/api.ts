import { useQuery } from "@tanstack/react-query";
import { useFilters } from "./store";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const TOKEN_KEY = "cih.token";
export const USER_KEY = "cih.user";

export function getToken(): string | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Expired / invalid session: drop credentials and bounce to the login page. */
function handleUnauthorized() {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
  } catch {}
  if (window.location.pathname !== "/login") window.location.href = "/login";
}

async function request(path: string, init: RequestInit = {}, json = true): Promise<Response> {
  const token = getToken();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(json ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (res.status === 401 && path !== "/api/login") handleUnauthorized();
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {}
    throw new ApiError(res.status, detail);
  }
  return res;
}

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  return (await request(path, init)).json();
}

export const post = <T = any>(path: string, body: unknown) => api<T>(path, { method: "POST", body: JSON.stringify(body) });

/** Multipart upload (the browser sets the multipart boundary, so no JSON content type). */
export async function upload<T = any>(path: string, form: FormData): Promise<T> {
  return (await request(path, { method: "POST", body: form }, false)).json();
}

/** Authenticated streaming POST (Server-Sent Events); the caller reads `res.body`. */
export async function postStream(path: string, body: unknown, signal?: AbortSignal): Promise<Response> {
  return request(path, { method: "POST", body: JSON.stringify(body), signal });
}

/** Authenticated file download. */
export async function download(path: string, filename: string) {
  const blob = await (await request(path)).blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

export interface User {
  email: string;
  name: string;
  role: "admin" | "engineer" | "executive";
  role_title: string;
  permissions: string[];
}

export interface DatasetStatus {
  source: "uploaded" | "benchmark";
  label: string;
  filename: string | null;
  rows: number;
  config_params: number;
  random_vars: number;
  synthetic_columns: string[];
  leakage_dropped?: string[];
  activated_at: string;
  version: number;
}

// ---- types (subset of backend payloads used across views)
export interface Meta {
  n_runs: number;
  n_config_params: number;
  n_random_vars: number;
  n_profiles: number;
  log_lines: number;
  generated_at: string;
  key_params: { name: string; kind: string; choices: (string | number)[]; desc: string }[];
  sandbox_params: string[];
  choices: Record<string, (string | number)[]>;
  defaults: Record<string, string | number>;
  model: { engine: string; auc: number; train_seconds: number };
  ai: { gemini_configured: boolean };
  dataset: DatasetStatus;
  filters: { environment: string[]; hardware: string[]; workload: string[]; date_min: string; date_max: string };
}

export interface ShapItem {
  feature: string;
  value: string | number;
  contribution: number;
}

export const useMeta = () => useQuery<Meta>({ queryKey: ["meta"], queryFn: () => api("/api/meta") });

/** Active dashboard filters in backend form ({hardware, environment, workload, date_from, date_to}), empty keys dropped. */
export function useActiveFilters(): Record<string, string> {
  const f = useFilters();
  return Object.fromEntries(
    Object.entries({ environment: f.environment, hardware: f.hardware, workload: f.workload, date_from: f.dateFrom, date_to: f.dateTo }).filter(([, v]) => !!v),
  ) as Record<string, string>;
}

function useFilterQs() {
  return new URLSearchParams(useActiveFilters()).toString();
}

export function useOverview() {
  const qs = useFilterQs();
  return useQuery({ queryKey: ["overview", qs], queryFn: () => api(`/api/overview?${qs}`), placeholderData: (p) => p });
}

// ---- evidence layer (filter-aware)
export interface Insight {
  id: string;
  title: string;
  category: string;
  severity: "low" | "medium" | "high" | "critical";
  finding: string;
  evidence: Record<string, any>;
  evidence_strength?: string;
  affected_parameters: string[];
  failure_signatures: string[];
  supporting_runs?: number;
  recommendation?: string;
  explanation?: string;
}

export function useInsights() {
  const qs = useFilterQs();
  return useQuery<{ insights: Insight[]; runs: number; baseline_failure_rate: number; filters: Record<string, string> }>({
    queryKey: ["insights", qs],
    queryFn: () => api(`/api/insights?${qs}`),
    placeholderData: (p) => p,
  });
}

export function useInsightSection<T = any>(section: string) {
  const qs = useFilterQs();
  return useQuery<T>({ queryKey: ["insights", section, qs], queryFn: () => api(`/api/insights/${section}?${qs}`), placeholderData: (p) => p });
}

export const useDataQuality = () => useQuery<any>({ queryKey: ["data-quality"], queryFn: () => api("/api/data-quality") });
export const useModelValidation = () => useQuery<any>({ queryKey: ["model-validation"], queryFn: () => api("/api/model/validation") });

export const useSummary = () => useQuery({ queryKey: ["summary"], queryFn: () => api("/api/summary"), enabled: false });
export const useDiscovery = () => useQuery({ queryKey: ["discovery"], queryFn: () => api("/api/discovery") });
export const useRandomization = () => useQuery({ queryKey: ["randomization"], queryFn: () => api("/api/randomization") });
export const useDrift = () => useQuery({ queryKey: ["drift"], queryFn: () => api("/api/drift") });
export const useRootCause = () => useQuery({ queryKey: ["rootcause"], queryFn: () => api("/api/rootcause") });
