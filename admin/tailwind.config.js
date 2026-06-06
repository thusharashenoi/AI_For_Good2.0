/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        // Blood Warriors palette (sampled from bloodwarriors.in)
        brand: "#f14164",
        "brand-dark": "#d62a4d",
        "brand-soft": "#fff5f7",
        ink: "#141414",
        body: "#3b3b3b",
        muted: "#8a8a8a",
        line: "#ececec",
        surface: "#ffffff",
        canvas: "#fafaf9",
        success: "#10b981",
        "success-dark": "#059669",
        warn: "#f59e0b",
        danger: "#b91c1c",
      },
      fontFamily: {
        sans: ["Manrope", "ui-sans-serif", "system-ui", "sans-serif"],
        head: ["Poppins", "Manrope", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(20,20,20,0.04), 0 8px 24px rgba(20,20,20,0.05)",
      },
    },
  },
  plugins: [],
};
