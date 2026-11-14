import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 在 Docker 里 API 地址是 http://api:8000；直接在本机跑时是 localhost
const target = process.env.API_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': target },
    // 代码放在 Windows 盘（/mnt/c）时热更新可能失灵，设 VITE_POLL=1 改用轮询
    watch: process.env.VITE_POLL ? { usePolling: true, interval: 300 } : undefined,
  },
})
