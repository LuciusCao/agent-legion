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
              return 'vendor'
            }
          },
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test-setup.ts'],
      coverage: {
        provider: 'v8',
        reporter: ['text', 'json', 'html'],
        reportsDirectory: './coverage',
        thresholds: {
          lines: 86,
          functions: 80,
          branches: 72,
          statements: 82,
        },
        exclude: [
          'node_modules/',
          'src/**/*.d.ts',
          'src/test-setup.ts',
          'src/**/*.test.ts',
          'src/**/*.test.tsx',
        ],
      },
    },
  }
})
