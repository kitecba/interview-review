import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // SSE (EventSource) 复用同一个代理即可，默认不缓冲。
        // 注意：ws 保持默认（false），SSE 走的是普通 HTTP 流。
        // 若后续发现 SSE 被缓冲/延迟，可在此处排查（例如确认后端未压缩、
        // 未被中间件聚合，或在必要时调整 proxy 的超时与压缩行为）。
      },
    },
  },
})
