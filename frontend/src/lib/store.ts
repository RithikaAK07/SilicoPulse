import { create } from "zustand";

interface Filters {
  environment: string;
  hardware: string;
  workload: string;
  dateFrom: string;
  dateTo: string;
  set: (patch: Partial<Omit<Filters, "set" | "reset">>) => void;
  reset: () => void;
}

const empty = { environment: "", hardware: "", workload: "", dateFrom: "", dateTo: "" };

export const useFilters = create<Filters>((set) => ({
  ...empty,
  set: (patch) => set(patch),
  reset: () => set(empty),
}));

export interface ChatChart {
  title: string;
  chart_type?: "bar" | "horizontal_bar" | "line";
  labels: string[];
  series: { name: string; values: number[] }[];
  y_label?: string;
}

export interface ChatStep {
  name: string;
  label: string;
  done: boolean;
  ok?: boolean;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  status?: string;
  steps?: ChatStep[];
  charts?: ChatChart[];
  suggestions?: string[];
  source?: string;
  error?: { message: string; retryAfter?: number | null };
  intents?: string[];
  filters?: Record<string, string>;
  evidence?: { title: string; category: string; severity: string; finding: string; evidence_strength?: string | null; evidence: Record<string, any> }[];
  grounding?: { figures: number; matched: number; unmatched: string[] };
}

interface Chat {
  messages: ChatMessage[];
  push: (m: ChatMessage) => void;
  update: (id: string, patch: Partial<ChatMessage>) => void;
  mutate: (id: string, fn: (m: ChatMessage) => Partial<ChatMessage>) => void;
  remove: (id: string) => void;
  clear: () => void;
}

export const useChat = create<Chat>((set) => ({
  messages: [],
  push: (m) => set((s) => ({ messages: [...s.messages, m] })),
  update: (id, patch) => set((s) => ({ messages: s.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)) })),
  mutate: (id, fn) => set((s) => ({ messages: s.messages.map((m) => (m.id === id ? { ...m, ...fn(m) } : m)) })),
  remove: (id) => set((s) => ({ messages: s.messages.filter((m) => m.id !== id) })),
  clear: () => set({ messages: [] }),
}));
