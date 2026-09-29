import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // `npm run dev` 时把 API 请求转给本机直接跑的 uvicorn（默认 8000 端口）。
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
