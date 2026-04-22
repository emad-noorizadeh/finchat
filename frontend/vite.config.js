import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: parseInt(env.VITE_PORT || '6001'),
      proxy: {
        '/api': {
          target: env.VITE_API_URL || 'http://localhost:6000',
          changeOrigin: true,
        },
      },
    },
  }
})
