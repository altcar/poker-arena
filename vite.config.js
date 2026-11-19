import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The React SPA is built into dist/. The Worker serves dist/ as static assets
// and handles /api/* itself. In development, Vite proxies /api to wrangler.
export default defineConfig({
  plugins: [react()],
  publicDir: "public",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8787",
    },
  },
  preview: {
    port: 4173,
    proxy: {
      "/api": "https://poker-arena.hheylau.workers.dev",
    },
  },
});
