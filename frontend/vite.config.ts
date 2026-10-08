import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // 本地开发时代理 /api 到后端 8000，并去掉 /api 前缀对齐后端真实路由。
      // 显式用 127.0.0.1 而不是 localhost：Windows 下 localhost 优先解析到 ::1，
      // 若 IPv6 的 8000 被其它进程/容器占用，请求会被静默转给错误的服务，
      // 表现为接口 404/405 而后端日志里什么都没有。
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
})
