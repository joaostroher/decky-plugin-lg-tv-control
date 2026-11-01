import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const local = path => fileURLToPath(new URL(path, import.meta.url));
export default defineConfig({
  root: local("./"),
  resolve: { alias: {
    "@decky/ui": local("./decky-ui.tsx"),
    "@decky/api": local("./decky-api.ts"),
  } },
  server: {
    host: "127.0.0.1", port: 5173, strictPort: true,
    proxy: { "/rpc/": { target: "http://127.0.0.1:8081", changeOrigin: true } },
  },
  build: { outDir: local("../out/local-preview"), emptyOutDir: true },
});
