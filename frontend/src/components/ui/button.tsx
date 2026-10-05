import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/**
 * primary   = red gradient CTA (exactly ONE per view)
 * default   = black gradient (weighty secondary actions: downloads, resets)
 * secondary = white with grey border
 * outline   = alias of secondary (kept for existing call sites)
 * ghost     = no fill, grey text
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[10px] text-sm font-semibold transition-[color,background-color,border-color,box-shadow,transform,filter] duration-150 focus-visible:outline-none focus-visible:shadow-focus disabled:pointer-events-none disabled:opacity-50 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-grad-red text-white shadow-cta hover:-translate-y-px hover:brightness-110 active:translate-y-0 active:brightness-100 active:shadow-none",
        default: "bg-grad-black text-white hover:-translate-y-px hover:brightness-125 active:translate-y-0 active:brightness-100",
        secondary: "border border-grey-300 bg-white text-black hover:border-black",
        outline: "border border-grey-300 bg-white text-black hover:border-black",
        ghost: "text-grey-600 hover:bg-grey-100 hover:text-black",
      },
      size: { default: "h-10 px-4", sm: "h-8 px-3 text-xs", lg: "h-11 px-6", icon: "h-10 w-10" },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, ...props }, ref) => (
  <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
));
Button.displayName = "Button";
