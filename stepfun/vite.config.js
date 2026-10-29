import { defineConfig } from "vite";

export default defineConfig({
  server: {
    host: "127.0.0.1",
    port: 43123,
    headers: {
      "Cross-Origin-Opener-Policy": "same-origin",
      "Cross-Origin-Embedder-Policy": "credentialless",
    },
    proxy: {
      "/api": "http://127.0.0.1:43124",
    },
  },
  optimizeDeps: {
    exclude: ["@huggingface/transformers"],
  },
});
