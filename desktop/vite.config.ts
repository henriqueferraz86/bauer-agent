import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Build emite direto para bauer/static/ — o `bauer serve` já serve essa pasta
// em "/" e monta /static. Em dev, o SPA roda no Vite (:5173) e o proxy manda as
// rotas de API para o `bauer serve`. O alvo pode ser sobrescrito com
// VITE_BAUER_API_TARGET (por exemplo, http://127.0.0.1:5174 no launch local).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backendTarget = env.VITE_BAUER_API_TARGET || "http://127.0.0.1:8000";

  return {
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
          (p) => [p, { target: backendTarget, changeOrigin: true }]
        )
      ),
    },
  };
});
