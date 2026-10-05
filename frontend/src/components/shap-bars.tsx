import { STATUS } from "@/lib/utils";
import type { ShapItem } from "@/lib/api";

/** SHAP-style bars: positive contributions raise failure risk, negative lower it (log-odds). */
export function ShapBars({ items }: { items: ShapItem[] }) {
  const max = Math.max(0.01, ...items.map((i) => Math.abs(i.contribution)));
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-[10px] uppercase tracking-wide text-slate-500">
        <span>← lowers risk</span>
        <span>raises risk →</span>
      </div>
      {items.map((s) => {
        const w = (Math.abs(s.contribution) / max) * 50;
        const up = s.contribution > 0;
        return (
          <div
            key={s.feature}
            className="group grid grid-cols-[minmax(0,11rem)_1fr_3.5rem] items-center gap-2 text-xs"
            title={`${s.feature}=${s.value}: ${up ? "+" : ""}${s.contribution.toFixed(3)} log-odds`}
          >
            <span className="truncate text-slate-700">
              {s.feature}=<span className="text-slate-900">{String(s.value)}</span>
            </span>
            <div className="relative h-4 rounded bg-slate-100">
              <div className="absolute inset-y-0 left-1/2 w-px bg-slate-600" />
              <div
                className="absolute inset-y-0.5 rounded-sm transition-all group-hover:brightness-125"
                style={{ background: up ? STATUS.critical : "#2a78d6", width: `${w}%`, left: up ? "50%" : `${50 - w}%` }}
              />
            </div>
            <span className="text-right tabular-nums text-slate-700">{(up ? "+" : "") + s.contribution.toFixed(2)}</span>
          </div>
        );
      })}
    </div>
  );
}
