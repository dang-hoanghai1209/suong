import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api/v1/plan-only": {
        target: "http://127.0.0.1:8765",
        changeOrigin: false,
        ws: false,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
    restoreMocks: true,
    clearMocks: true,
  },
});
