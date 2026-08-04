import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Fixed port, never auto-increment. If it is occupied, fail loudly (PRD §13.2).
const WEB_PORT = Number(process.env.ALIE_WEB_PORT ?? 5472);
const API_PORT = Number(process.env.ALIE_API_PORT ?? 8471);

export default defineConfig({
  plugins: [react()],
  server: {
    port: WEB_PORT,
    strictPort: true,
    proxy: {
      // The UI talks to same-origin /api so there is no CORS surface in dev.
      "/api": {
        target: `http://127.0.0.1:${API_PORT}`,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
