import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { rewriteAgentChatProxyPath } from "./src/agent-chat-url.mjs";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  return {
    // GitHub Pages project sites are hosted below /<repository-name>/.
    // Local development and other static hosts keep the normal root path.
    base: env.VITE_BASE_PATH || "/",
    plugins: [react()],
    server: {
      proxy: {
        "/api": {
          target: env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000",
          changeOrigin: true,
          rewrite: (path) => rewriteAgentChatProxyPath(path).replace(/^\/api/, ""),
        },
      },
    },
  };
});
