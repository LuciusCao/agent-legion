import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react()],
    css: {
      modules: {
        localsConvention: 'dashes',
      },
    },
    server: {
      proxy: {
        '/api': {
          target: apiTarget,
          // changeOrigin is required so the backend sees the Host header it expects
          // (some ASGI/WebSocket paths rely on this in dev)
          changeOrigin: true,
          ws: true,
        },
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes('node_modules')) {
              if (id.includes('react') || id.includes('react-router-dom')) {
                return 'vendor-react'
              }
              if (id.includes('@material/web')) {
                return 'vendor-material'
              }
            }
          },
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test-setup.ts'],
    },
  }
})
