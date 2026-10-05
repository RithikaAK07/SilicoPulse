import * as React from "react";
import { cn } from "@/lib/utils";

export function Badge({ className, color, children, ...props }: React.HTMLAttributes<HTMLSpanElement> & { color?: string }) {
  return (
    <span
      className={cn("inline-flex items-center gap-1.5 rounded-md border border-grey-200 bg-white px-2 py-0.5 text-2xs font-semibold text-grey-800", className)}
      {...props}
    >
      {color && <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: color }} aria-hidden />}
      {children}
    </span>
  );
}
