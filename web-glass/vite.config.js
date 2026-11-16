import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built to dist/ and served by the Horus API at /glass (offline, no CDN).
// `npm run dev` proxies the API so the UI can be developed against `make serve`.
export default defineConfig({
  base: "/glass/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
