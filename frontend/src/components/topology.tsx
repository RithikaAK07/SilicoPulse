"use client";
import { useState } from "react";
import { num, pct, riskStatus } from "@/lib/utils";

interface Node {
  hardware: string;
  environment: string;
  runs: number;
  fail_rate: number;
  temp: number | null;
  tput: number;
  top_signature: string | null;
}

/** 2D hardware topology: test host → environments → device fleets, colored by failure-risk status. */
export function Topology({ nodes }: { nodes: Node[] }) {
  const envs = Array.from(new Set(nodes.map((n) => n.environment)));
  const hws = Array.from(new Set(nodes.map((n) => n.hardware)));
  const [sel, setSel] = useState<Node | null>(null);
  const W = 760;
  const H = 360;
  const envX = (i: number) => ((i + 0.5) / envs.length) * W;
  const leafX = (ei: number, hi: number) => envX(ei) + (hi - (hws.length - 1) / 2) * (W / envs.length / (hws.length + 0.3));
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_16rem]">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Hardware topology colored by failure risk">
        <g>
          {envs.map((e, ei) => (
            <line key={e} x1={W / 2} y1={46} x2={envX(ei)} y2={140} stroke="#D4D4DA" strokeWidth={1.5} />
          ))}
          {nodes.map((n) => {
            const ei = envs.indexOf(n.environment);
            const hi = hws.indexOf(n.hardware);
            return <line key={n.environment + n.hardware} x1={envX(ei)} y1={140} x2={leafX(ei, hi)} y2={270} stroke="#E7E7EB" strokeWidth={1.5} />;
          })}
        </g>
        <g>
          <rect x={W / 2 - 70} y={18} width={140} height={34} rx={8} fill="#0E0E10" stroke="#0E0E10" />
          <text x={W / 2} y={40} textAnchor="middle" fill="#FFFFFF" fontSize={13} fontWeight={600}>
            Test Orchestrator
          </text>
          {envs.map((e, ei) => (
            <g key={e}>
              <rect x={envX(ei) - 55} y={124} width={110} height={30} rx={8} fill="#FFFFFF" stroke="#D4D4DA" />
              <text x={envX(ei)} y={144} textAnchor="middle" fill="#0E0E10" fontSize={12}>
                {e}
              </text>
            </g>
          ))}
          {nodes.map((n) => {
            const ei = envs.indexOf(n.environment);
            const hi = hws.indexOf(n.hardware);
            const x = leafX(ei, hi);
            const st = riskStatus(n.fail_rate);
            const r = 10 + Math.sqrt(n.runs) / 3.2;
            const active = sel?.environment === n.environment && sel?.hardware === n.hardware;
            return (
              <g key={n.environment + n.hardware} className="cursor-pointer" onMouseEnter={() => setSel(n)} onClick={() => setSel(n)}>
                <circle cx={x} cy={270} r={r + 6} fill={st.color} opacity={0.15} />
                <circle cx={x} cy={270} r={r} fill={st.color} stroke={active ? "#DC2626" : "#FFFFFF"} strokeWidth={2} />
                <text x={x} y={274} textAnchor="middle" fill={st.onColor} fontSize={11.5} fontWeight={700}>
                  {Math.round(n.fail_rate * 100)}%
                </text>
                <text x={x} y={270 + r + 18} textAnchor="middle" fill="#52525B" fontSize={11.5}>
                  {n.hardware.replace("-NVMe", "")}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
      <div className="space-y-3 text-xs">
        <div className="flex flex-wrap gap-2">
          {[0.05, 0.15, 0.3, 0.5].map((v) => {
            const s = riskStatus(v);
            return (
              <span key={s.label} className="flex items-center gap-1.5 text-grey-500">
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: s.color }} /> {s.label}
              </span>
            );
          })}
        </div>
        {sel ? (
          <div className="rounded-lg border border-grey-200 bg-grey-50 p-3">
            <div className="text-sm font-semibold text-black">{sel.hardware}</div>
            <div className="mb-2 text-grey-500">{sel.environment} environment</div>
            <dl className="grid grid-cols-2 gap-y-1">
              <dt className="text-grey-500">Runs</dt><dd className="text-right">{num(sel.runs)}</dd>
              <dt className="text-grey-500">Failure rate</dt><dd className="text-right" style={{ color: riskStatus(sel.fail_rate).ink }}>{pct(sel.fail_rate)} · {riskStatus(sel.fail_rate).label}</dd>
              <dt className="text-grey-500">Avg temp</dt><dd className="text-right">{sel.temp == null ? "–" : `${sel.temp}°C`}</dd>
              <dt className="text-grey-500">Throughput</dt><dd className="text-right">{num(sel.tput)} MB/s</dd>
              <dt className="text-grey-500">Top failure</dt><dd className="truncate text-right font-mono text-2xs">{sel.top_signature ?? "–"}</dd>
            </dl>
          </div>
        ) : (
          <p className="text-grey-500">Hover a device fleet to inspect it. Node size scales with run count.</p>
        )}
      </div>
    </div>
  );
}
