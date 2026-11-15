import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode, command }) => {
  const env = loadEnv(mode, ".", ["API_", "DEV_"]);
  const devToken =
    command === "serve" && mode !== "production"
      ? env.DEV_AUTH_TOKEN
      : undefined;
  const autoAuth = !!devToken && /^[\x21-\x7e]{32,}$/.test(devToken);
  return {
    plugins: [
      react(),
      {
        name: "local-dev-session",
        configureServer(server) {
          server.middlewares.use("/__dev/session", (req, res) => {
            res.setHeader("Cache-Control", "no-store");
            res.setHeader("Content-Type", "application/json");
            if (req.method !== "GET") {
              res.statusCode = 405;
              res.end();
              return;
            }
            res.end(JSON.stringify({ enabled: autoAuth }));
          });
        },
      },
    ],
    server: {
      allowedHosts: true,
      cors: true,
      proxy: {
        "/api": {
          target: env.API_PROXY_TARGET ?? "http://127.0.0.1:8000",
          changeOrigin: true,
          ws: true,
          configure(proxy) {
            proxy.on("proxyReq", (proxyReq, req) => {
              const useDevSession = req.headers["x-guuey-dev-session"] === "1";
              proxyReq.removeHeader("X-Guuey-Dev-Session");
              if (autoAuth && useDevSession && !req.headers.authorization)
                proxyReq.setHeader("Authorization", `Bearer ${devToken}`);
            });
            proxy.on("proxyReqWs", (proxyReq, req) => {
              proxyReq.setHeader("X-Forwarded-Host", req.headers.host ?? "");
              const origin = req.headers.origin;
              const sameOrigin =
                origin === `http://${req.headers.host}` ||
                origin === `https://${req.headers.host}`;
              if (autoAuth && sameOrigin)
                proxyReq.setHeader("Authorization", `Bearer ${devToken}`);
            });
          },
        },
      },
    },
  };
});
