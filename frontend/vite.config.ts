import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the Django API runs on :18731; in production Netlify proxies /api.
export default defineConfig({
  plugins: [react()],
  build: {
    // The map library is most of the bundle; in its own file it stays cached across our deploys.
    rollupOptions: { output: { manualChunks: { maplibre: ["maplibre-gl"] } } },
    chunkSizeWarningLimit: 1100,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:18731" },
  },
});
