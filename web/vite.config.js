import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发模式：Vite 5173，/api 代理到本地 FastAPI（story-sim serve --dev）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
})
