import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, "onChange"> {
  options: (string | number)[];
  onValueChange: (v: string) => void;
  placeholder?: string;
}

export function Select({ options, onValueChange, placeholder, className, ...props }: Props) {
  return (
    <div className={cn("relative", className)}>
      <select
        className="h-9 w-full appearance-none rounded-lg border border-slate-300 bg-white pl-3 pr-8 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-sky-500"
        onChange={(e) => onValueChange(e.target.value)}
        {...props}
      >
        {placeholder !== undefined && <option value="">{placeholder}</option>}
        {options.map((o) => (
          <option key={String(o)} value={String(o)}>
            {String(o)}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2.5 top-2.5 h-4 w-4 text-slate-500" />
    </div>
  );
}
