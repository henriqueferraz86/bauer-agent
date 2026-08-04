import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build emite direto para bauer/static/ — o `bauer serve` já serve essa pasta
// em "/" e monta /static. Em dev, o SPA roda no Vite (:5173) e o proxy manda as
// rotas de API para o serve de DESENVOLVIMENTO em :5174 — porta escolhida em
// .claude/launch.json justamente para não colidir com um serve de produção no
// default :8000 (bauer/config_loader.py, ServeSection.port).
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
      ["/api", "/chat", "/transcribe", "/stream", "/health", "/status", "/models", "/sessions", "/tools", "/v1"].map(
        (p) => [p, { target: "http://127.0.0.1:5174", changeOrigin: true }]
      )
    ),
  },
});
