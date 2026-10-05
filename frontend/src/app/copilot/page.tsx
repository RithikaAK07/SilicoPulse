"use client";
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Bot, Check, ChevronDown, Loader2, RotateCcw, Send, Sparkles, Square, Trash2, User, Wrench } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { postStream } from "@/lib/api";
import { useChat, type ChatChart, type ChatMessage } from "@/lib/store";
import { useAuth } from "@/context/AuthContext";
import { INK, SERIES, cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ChartTooltip, Markdown, axisProps } from "@/components/common";

const STARTERS = [
  "Give me a quick health check of this test campaign",
  "Which settings drive failures the most? Show a chart.",
  "Why did configuration CFG-0001 fail more often than CFG-0002?",
  "Find the top 3 risky seeds and tell me if they're deterministic",
  "Recommend a configuration with the highest throughput and lowest failure rate",
  "What's the failure rate per workload on Gen5 drives?",
];

function humanDelay(sec: number) {
  if (sec < 90) return `${Math.ceil(sec)} seconds`;
  if (sec < 5400) return `${Math.round(sec / 60)} minutes`;
  return `${(sec / 3600).toFixed(1)} hours (daily free-tier quota)`;
}

/** Parse a Server-Sent Events stream of JSON `data:` lines. */
async function* sse(res: Response): AsyncGenerator<any> {
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of chunk.split("\n")) if (line.startsWith("data: ")) yield JSON.parse(line.slice(6));
    }
  }
}

export default function CopilotPage() {
  const { messages, push, mutate, remove, clear } = useChat();
  const { user } = useAuth();
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    // braces matter: Chrome's scrollIntoView now returns a Promise, which React would treat as a cleanup function
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
  }, [input]);

  async function send(text: string, history: ChatMessage[] = useChat.getState().messages) {
    const q = text.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    const userMsg: ChatMessage = { id: crypto.randomUUID(), role: "user", content: q };
    push(userMsg);
    const id = crypto.randomUUID();
    push({ id, role: "assistant", content: "", streaming: true, status: "Connecting to SilicoPulse AI…", steps: [], charts: [] });

    // conversation memory: prior turns that produced an answer
    const convo = [...history.filter((m) => m.content.trim()), userMsg].map((m) => ({
      role: m.role,
      // tell the model which visuals it already showed, so follow-ups don't redraw them
      content: m.charts?.length ? `${m.content}

[Displayed chart(s): ${m.charts.map((c) => c.title).join("; ")}]` : m.content,
    }));
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const res = await postStream("/api/copilot/chat", { messages: convo.slice(-24) }, ctrl.signal);
      for await (const e of sse(res)) {
        switch (e.type) {
          case "status":
            mutate(id, () => ({ status: e.text }));
            break;
          case "tool":
            mutate(id, (m) => ({ status: `${e.label}…`, steps: [...(m.steps ?? []), { name: e.name, label: e.label, done: false }] }));
            break;
          case "tool_done":
            mutate(id, (m) => {
              const steps = [...(m.steps ?? [])];
              const i = steps.findIndex((s) => s.name === e.name && !s.done);
              if (i >= 0) steps[i] = { ...steps[i], done: true, ok: e.ok };
              return { steps };
            });
            break;
          case "text":
            mutate(id, (m) => ({ content: m.content + e.delta, status: undefined }));
            break;
          case "chart":
            mutate(id, (m) => ({ charts: [...(m.charts ?? []), e.chart] }));
            break;
          case "suggestions":
            mutate(id, () => ({ suggestions: e.items }));
            break;
          case "error":
            mutate(id, () => ({ error: { message: e.message, retryAfter: e.retry_after } }));
            break;
          case "done":
            mutate(id, () => ({ source: e.source }));
            break;
        }
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") mutate(id, (m) => ({ content: m.content || "_Stopped._" }));
      else mutate(id, () => ({ error: { message: (err as Error).message } }));
    } finally {
      mutate(id, (m) => ({ streaming: false, status: undefined, steps: (m.steps ?? []).map((s) => ({ ...s, done: true })) }));
      abortRef.current = null;
      setBusy(false);
      taRef.current?.focus();
    }
  }

  function retry(assistantId: string) {
    const all = useChat.getState().messages;
    const idx = all.findIndex((m) => m.id === assistantId);
    const userMsg = all[idx - 1];
    if (!userMsg || userMsg.role !== "user") return;
    remove(assistantId);
    remove(userMsg.id);
    send(userMsg.content, all.slice(0, idx - 1));
  }

  const firstName = user?.name.split(" ")[0];
  return (
    <div className="flex h-[calc(100vh-5.5rem)] flex-col">
      <Card className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="flex items-center gap-3 border-b border-slate-200 px-5 py-3">
          <div className="relative grid h-10 w-10 place-items-center rounded-full bg-gradient-to-br from-sky-500 to-indigo-600">
            <Bot className="h-5 w-5 text-white" />
            <span className="absolute bottom-0 right-0 h-3 w-3 rounded-full border-2 border-white bg-emerald-500" />
          </div>
          <div className="flex-1">
            <div className="text-sm font-semibold text-slate-900">SilicoPulse AI</div>
            <div className="text-xs text-slate-500">Gemini-powered copilot · analyzes your live execution data with tools, SQL &amp; ML models</div>
          </div>
          {messages.length > 0 && (
            <Button variant="ghost" size="sm" onClick={() => !busy && clear()} disabled={busy}>
              <Trash2 className="h-4 w-4" /> New chat
            </Button>
          )}
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto bg-slate-50/60 px-4 py-5 sm:px-6">
          {!messages.length && (
            <div className="mx-auto max-w-2xl py-8 text-center">
              <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-sky-500 to-indigo-600 shadow-lg">
                <Sparkles className="h-7 w-7 text-white" />
              </div>
              <h2 className="mt-4 text-xl font-semibold text-slate-900">Hi{firstName ? ` ${firstName}` : ""}, what should we dig into?</h2>
              <p className="mt-1 text-sm text-slate-500">
                Ask anything about the active dataset in plain English. I'll pick the right analyses, run them, and explain what I find.
              </p>
              <div className="mt-6 grid gap-2 text-left sm:grid-cols-2">
                {STARTERS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 shadow-sm transition-colors hover:border-sky-300 hover:bg-sky-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) =>
            m.role === "user" ? (
              <UserBubble key={m.id} m={m} />
            ) : (
              <AssistantBubble key={m.id} m={m} last={i === messages.length - 1} onSuggest={(q) => send(q)} onRetry={() => retry(m.id)} busy={busy} />
            ),
          )}
          <div ref={endRef} />
        </div>

        <form
          className="border-t border-slate-200 bg-white p-3 sm:p-4"
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
        >
          <div className="flex items-end gap-2 rounded-2xl border border-slate-300 bg-white px-3 py-2 shadow-sm focus-within:border-sky-400 focus-within:ring-2 focus-within:ring-sky-100">
            <textarea
              ref={taRef}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
              }}
              placeholder="Message SilicoPulse AI…  (Enter to send, Shift+Enter for a new line)"
              className="max-h-40 flex-1 resize-none bg-transparent py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none"
              aria-label="Message"
            />
            {busy ? (
              <Button type="button" size="icon" variant="secondary" onClick={() => abortRef.current?.abort()} aria-label="Stop generating" title="Stop">
                <Square className="h-4 w-4 fill-current" />
              </Button>
            ) : (
              <Button type="submit" size="icon" disabled={!input.trim()} aria-label="Send">
                <Send className="h-4 w-4" />
              </Button>
            )}
          </div>
          <p className="mt-1.5 px-1 text-[11px] text-slate-400">AI answers are generated by Gemini from tool results on your data; verify critical decisions.</p>
        </form>
      </Card>
    </div>
  );
}

function UserBubble({ m }: { m: ChatMessage }) {
  return (
    <div className="flex justify-end gap-3">
      <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-tr-md bg-sky-600 px-4 py-2.5 text-sm text-white shadow-sm">{m.content}</div>
      <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-200">
        <User className="h-4 w-4 text-slate-600" />
      </div>
    </div>
  );
}

function AssistantBubble({ m, last, onSuggest, onRetry, busy }: { m: ChatMessage; last: boolean; onSuggest: (q: string) => void; onRetry: () => void; busy: boolean }) {
  const [showSteps, setShowSteps] = useState(false);
  const steps = m.steps ?? [];
  const thinking = m.streaming && !m.content;
  return (
    <div className="flex gap-3">
      <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-sky-500 to-indigo-600">
        <Bot className="h-4 w-4 text-white" />
      </div>
      <div className="min-w-0 max-w-4xl flex-1">
        <div className="rounded-2xl rounded-tl-md border border-slate-200 bg-white px-4 py-3 shadow-sm">
          {steps.length > 0 && (
            <div className="mb-2">
              <button onClick={() => setShowSteps(!showSteps)} className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700">
                <Wrench className="h-3.5 w-3.5" />
                {m.streaming && steps.some((s) => !s.done) ? "Analyzing" : "Analysis"}: {steps.length} step{steps.length > 1 ? "s" : ""}
                <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", (showSteps || thinking) && "rotate-180")} />
              </button>
              {(showSteps || thinking) && (
                <ul className="mt-1.5 space-y-1 border-l-2 border-slate-100 pl-3">
                  {steps.map((s, i) => (
                    <li key={i} className="flex items-center gap-1.5 text-xs text-slate-600">
                      {s.done ? (
                        s.ok === false ? <AlertTriangle className="h-3.5 w-3.5 text-amber-500" /> : <Check className="h-3.5 w-3.5 text-emerald-600" />
                      ) : (
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-600" />
                      )}
                      {s.label}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {thinking && !m.error && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <span className="flex gap-1">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-sky-500 [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-sky-500 [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-sky-500" />
              </span>
              {m.status ?? "Thinking…"}
            </div>
          )}

          {m.content && (
            <>
              <Markdown>{m.content}</Markdown>
              {m.streaming && <span className="ml-0.5 inline-block h-4 w-1.5 animate-blink bg-sky-500 align-middle" />}
            </>
          )}

          {(m.charts ?? []).map((c, i) => (
            <ChatChartView key={i} chart={c} />
          ))}

          {m.error && (
            <div className="mt-2 flex flex-wrap items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span className="flex-1">
                {m.error.message}
                {m.error.retryAfter ? ` Try again in about ${humanDelay(m.error.retryAfter)}.` : ""}
              </span>
              {last && (
                <Button size="sm" variant="outline" onClick={onRetry} disabled={busy}>
                  <RotateCcw className="h-3.5 w-3.5" /> Retry
                </Button>
              )}
            </div>
          )}
        </div>

        {!m.streaming && m.source && !m.error && (
          <div className="mt-1 px-1 text-[10px] text-slate-400">
            {m.source}
            {steps.length ? ` · ${steps.length} tool call${steps.length > 1 ? "s" : ""}` : ""}
          </div>
        )}

        {last && !m.streaming && (m.suggestions ?? []).length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {m.suggestions!.map((q) => (
              <button
                key={q}
                onClick={() => onSuggest(q)}
                disabled={busy}
                className="rounded-full border border-sky-200 bg-white px-3 py-1.5 text-xs text-sky-800 shadow-sm transition-colors hover:bg-sky-50 disabled:opacity-50"
              >
                {q}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ChatChartView({ chart }: { chart: ChatChart }) {
  const series = (chart.series ?? []).slice(0, 4);
  if (!chart.labels?.length || !series.length) return null;
  const data = chart.labels.map((label, i) => ({ label, ...Object.fromEntries(series.map((s) => [s.name, s.values?.[i] ?? null])) }));
  const horizontal = chart.chart_type === "horizontal_bar";
  const multi = series.length > 1;
  const height = horizontal ? Math.max(180, chart.labels.length * 28 + 50) : 240;
  return (
    <figure className="mt-3 rounded-xl border border-slate-200 p-3">
      <figcaption className="mb-2 text-xs font-semibold text-slate-700">{chart.title}</figcaption>
      <div style={{ height }}>
        <ResponsiveContainer>
          {chart.chart_type === "line" ? (
            <LineChart data={data} margin={{ left: 0, right: 12, top: 4 }}>
              <CartesianGrid stroke={INK.grid} vertical={false} />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis {...axisProps} label={chart.y_label ? { value: chart.y_label, angle: -90, position: "insideLeft", fill: INK.muted, fontSize: 11 } : undefined} />
              <Tooltip content={<ChartTooltip />} />
              {multi && <Legend wrapperStyle={{ fontSize: 12 }} />}
              {series.map((s, i) => (
                <Line key={s.name} isAnimationActive={false} dataKey={s.name} stroke={SERIES[i]} strokeWidth={2} dot={{ r: 3, fill: SERIES[i], strokeWidth: 0 }} />
              ))}
            </LineChart>
          ) : (
            <BarChart data={data} layout={horizontal ? "vertical" : "horizontal"} margin={{ left: horizontal ? 10 : 0, right: 24, top: 4, bottom: horizontal ? 0 : 24 }} barCategoryGap={multi ? 6 : 4}>
              <CartesianGrid stroke={INK.grid} horizontal={!horizontal} vertical={horizontal} />
              {horizontal ? (
                <>
                  <XAxis type="number" {...axisProps} />
                  <YAxis type="category" dataKey="label" {...axisProps} width={150} interval={0} />
                </>
              ) : (
                <>
                  <XAxis dataKey="label" {...axisProps} interval={0} angle={chart.labels.length > 6 ? -25 : 0} textAnchor={chart.labels.length > 6 ? "end" : "middle"} />
                  <YAxis {...axisProps} label={chart.y_label ? { value: chart.y_label, angle: -90, position: "insideLeft", fill: INK.muted, fontSize: 11 } : undefined} />
                </>
              )}
              <Tooltip content={<ChartTooltip />} cursor={{ fill: "#e2e8f0aa" }} />
              {multi && <Legend wrapperStyle={{ fontSize: 12 }} />}
              {series.map((s, i) => (
                <Bar key={s.name} isAnimationActive={false} dataKey={s.name} fill={SERIES[i]} radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={44} />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
