import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
//
// dev proxy 雙路由：
// /api → web.py (port 8799)
// /agentcore → AgentCore dev server (port 8080)
// /invocations → AgentCore dev server (port 8080)
//
// 開發環境預設只連本機，避免本機 UI 悄悄依賴 AWS。
// 需要連其他環境時再明確設定 VITE_API_PROXY_TARGET（僅影響 /api）。
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8799'
const agentcoreTarget = 'http://127.0.0.1:8080'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
      },
      '/agentcore': {
        target: agentcoreTarget,
        changeOrigin: true,
        rewrite: (path: string) => path.replace(/^\/agentcore/, ''),
      },
      '/invocations': {
        target: agentcoreTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    // 純靜態輸出，供既有 nginx/EC2 部署方案（見
    // docs/architecture/PLAN-frontend-backend-split.md §5）直接服務，不含 runtime。
    outDir: 'dist',
    sourcemap: false,
  },
})
