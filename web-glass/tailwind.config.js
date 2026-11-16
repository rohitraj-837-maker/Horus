/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "SF Pro Display", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["ui-monospace", "SF Mono", "Menlo", "monospace"],
      },
      colors: {
        ink: { DEFAULT: "#f2f2f5", 2: "rgba(242,242,245,.66)", 3: "rgba(242,242,245,.42)" },
        mod: "#ff9f2e",
        sev: "#ff453a",
        glow: "#ffd27a",
      },
      borderRadius: { glass: "28px" },
      backdropBlur: { glass: "28px" },
    },
  },
  plugins: [],
};
