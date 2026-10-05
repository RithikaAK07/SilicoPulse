import * as React from "react";
import { cn } from "@/lib/utils";

export function Badge({ className, color, children, ...props }: React.HTMLAttributes<HTMLSpanElement> & { color?: string }) {
  return (
    <span
      className={cn("inline-flex items-center gap-1 rounded-md border border-slate-300 bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-800", className)}
      {...props}
    >
      {color && <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: color }} aria-hidden />}
      {children}
    </span>
  );
}
