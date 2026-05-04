/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

// Build output goes to ../app/static/ so FastAPI can serve it from GET / per
// backend `app/main.py:85-98` (mounts /assets and serves index.html from
// app/static/). Brief: Phase D A2.1 stack swap.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: resolve(__dirname, "../app/static"),
    emptyOutDir: true,
    // Source maps off for prod: F-1 transparency uses /api/v1/version, not
    // bundle inspection; smaller bundle helps HF Space cold-start first paint.
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      // Local dev: proxy /api to backend at :7860 per spec §9.1 CMD.
      "/api": {
        target: "http://127.0.0.1:7860",
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
