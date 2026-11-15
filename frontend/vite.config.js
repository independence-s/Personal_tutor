import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxy = {
    '/api': {
      target: env.API_TARGET || 'http://127.0.0.1:8001',
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, ''),
    },
  }
  return {
    plugins: [vue()],
    server: { proxy },
    preview: { proxy },
    test: { environment: 'jsdom' },
  }
})
