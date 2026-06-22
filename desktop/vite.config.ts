import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build emite direto para bauer/static/ — o `bauer serve` já serve essa pasta
// em "/" e monta /static. Em dev, o proxy encaminha as rotas de API para o
// backend em :8000, então o SPA roda no Vite (:5173) falando com o serve real.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../bauer/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      ["/api", "/chat", "/stream", "/health", "/status", "/models", "/sessions", "/tools", "/v1"].map(
        (p) => [p, { target: "http://127.0.0.1:8000", changeOrigin: true }]
      )
    ),
  },
});
