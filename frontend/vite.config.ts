import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

// In development the API is proxied so the refresh-token cookie stays same-origin.
const target = process.env.VITE_API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    port: 5173,
    proxy: {
      "/api": { target, changeOrigin: true },
      "/health": { target, changeOrigin: true },
      "/docs": { target, changeOrigin: true },
      "/openapi.json": { target, changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
});
