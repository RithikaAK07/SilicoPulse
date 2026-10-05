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
        className="h-10 w-full appearance-none rounded-[10px] border border-grey-300 bg-white pl-3 pr-9 text-sm text-black hover:border-grey-400 focus:border-red-600 focus:shadow-focus focus:outline-none"
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
      <ChevronDown className="pointer-events-none absolute right-3 top-3 h-4 w-4 text-grey-500" strokeWidth={1.75} />
    </div>
  );
}
