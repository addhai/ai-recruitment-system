import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // 用 vite 自带的 loadEnv 读环境变量，不引入 @types/node（本项目 devDeps 里没有）
  const env = loadEnv(mode, '.', '')

  return {
    plugins: [react()],
    // 单测只覆盖纯函数（src/lib/），没有 DOM 依赖，用 node 环境即可，
    // 不必引入 jsdom / testing-library——那套在有组件测试需求时再加。
    test: {
      environment: 'node',
      include: ['src/**/*.test.ts'],
    },
    server: {
      proxy: {
        // 本地开发时代理 /api 到后端 8000，并去掉 /api 前缀对齐后端真实路由。
        // 显式用 127.0.0.1 而不是 localhost：Windows 下 localhost 优先解析到 ::1，
        // 若 IPv6 的 8000 被其它进程/容器占用，请求会被静默转给错误的服务，
        // 表现为接口 404/405 而后端日志里什么都没有。
        // 后端端口被占用时可覆盖：VITE_API_PROXY_TARGET=http://127.0.0.1:8010 npm run dev
        '/api': {
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api/, ''),
        },
      },
    },
  }
})
