import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
//
// dev proxy：把 /api/* 轉給後端（預設指向 live API，credit-safe：純轉發不落地
// 任何資料；本機起後端時可用 VITE_API_PROXY_TARGET 覆寫成 http://127.0.0.1:8080）。
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://13.211.110.218'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    // 純靜態輸出，供既有 nginx/EC2 部署方案（見
    // docs/PLAN-frontend-backend-split.md §5）直接服務，不含 runtime。
    outDir: 'dist',
    sourcemap: false,
  },
})
