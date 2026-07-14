/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Commercial SaaS build. Output to ./dist (Vercel static deploy per
// dispatches/D4_specs.md §6 Deliverable 1 — output dir frontend/dist).
// The F-1 `outDir: ../app/static` (FastAPI-served SPA) is retired per
// frontend/D4_rehydration_spec.md §6 (vite.config.ts row).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    // Local dev proxy retargets to the backend per D4_rehydration_spec.md §6.
    // In prod the client calls VITE_API_BASE_URL directly (CORS owned by
    // backend-dev clerk_auth.py exemptions + main.py allowlist).
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: true,
  },
});
