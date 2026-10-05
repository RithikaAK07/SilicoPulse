import type { Config } from "tailwindcss";

/**
 * SilicoPulse design tokens. The colour palette is REPLACED (not extended): only white, the
 * cool-neutral greys, brand red and black exist, so no blue/green/orange/purple class can render.
 * Values mirror the CSS variables in src/app/globals.css.
 */
const grey = {
  50: "#F7F7F9",
  100: "#F1F1F4",
  150: "#ECECEF", // table row borders, chart gridlines
  200: "#E7E7EB", // default borders
  300: "#D4D4DA",
  400: "#A1A1AA",
  500: "#71717A", // muted text, inactive icons (minimum text contrast on white)
  600: "#52525B", // secondary text
  700: "#3F3F46",
  800: "#27272A",
  900: "#0E0E10",
};

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      white: "#FFFFFF",
      black: "#0E0E10",
      "black-2": "#1C1C20",
      grey,
      red: {
        50: "#FFF1F1",
        100: "#FEE2E2",
        200: "#FECACA",
        300: "#FCA5A5",
        400: "#F87171",
        500: "#EF4444",
        600: "#DC2626",
        700: "#B91C1C",
        800: "#991B1B",
        900: "#7F1D1D",
      },
    },
    fontFamily: {
      sans: ["Alegreya", "Source Serif 4", "Newsreader", "Georgia", "Times New Roman", "serif"],
      serif: ["Alegreya", "Source Serif 4", "Newsreader", "Georgia", "Times New Roman", "serif"],
      mono: ["JetBrains Mono", "ui-monospace", "SF Mono", "Menlo", "monospace"],
    },
    fontSize: {
      // [size, line-height]; minimum anywhere is 11.5px
      "2xs": ["11.5px", "16px"],
      xs: ["12.5px", "18px"],
      sm: ["13.5px", "20px"],
      base: ["15px", "24px"],
      lg: ["16px", "22px"],
      xl: ["22px", "28px"],
      "2xl": ["26px", "32px"],
      "3xl": ["34px", "40px"],
      "4xl": ["40px", "44px"],
      "5xl": ["48px", "52px"],
    },
    extend: {
      backgroundImage: {
        "grad-page": "var(--grad-page)",
        "grad-sidebar": "var(--grad-sidebar)",
        "grad-red": "var(--grad-red)",
        "grad-black": "var(--grad-black)",
        "grad-fail": "var(--grad-fail)",
        "grad-login": "var(--grad-login)",
        "grad-banner": "var(--grad-banner)",
      },
      boxShadow: {
        card: "var(--shadow-card)",
        "card-hover": "var(--shadow-card-hover)",
        cta: "var(--shadow-cta)",
        focus: "var(--focus-ring)",
      },
      borderRadius: { xl2: "18px" },
      transitionDuration: { DEFAULT: "160ms" },
      keyframes: {
        blink: { "50%": { opacity: "0" } },
        shimmer: { "100%": { transform: "translateX(100%)" } },
        progress: { "0%": { transform: "translateX(-100%)" }, "100%": { transform: "translateX(250%)" } },
      },
      animation: {
        blink: "blink 1s step-end infinite",
        shimmer: "shimmer 1.4s infinite",
        progress: "progress 1.1s ease-in-out infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
