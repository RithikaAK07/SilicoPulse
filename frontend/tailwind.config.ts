import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: { sans: ["system-ui", "-apple-system", "Segoe UI", "sans-serif"] },
      keyframes: { blink: { "50%": { opacity: "0" } } },
      animation: { blink: "blink 1s step-end infinite" },
    },
  },
  plugins: [],
} satisfies Config;
