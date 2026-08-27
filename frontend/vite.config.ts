import { createLogger, defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const commonTestExcludes = [
  'stress/**',
  '**/node_modules/**',
  '**/dist/**',
  '**/cypress/**',
  '**/.{idea,git,cache,output,temp}/**',
  '**/{karma,rollup,webpack,vite,vitest,jest,ava,babel,nyc,cypress,tsup,build}.config.*',
]

// These .test.ts files exercise browser APIs or React hooks. All remaining
// .test.ts files are pure logic and run in the faster Node project.
const browserTestFiles = [
  'src/api/core.test.ts',
  'src/components/useJobListLoadMore.test.ts',
  'src/hooks/useDashboardEvents.test.ts',
  'src/hooks/useDebouncedCallback.test.ts',
  'src/hooks/useJobComprehensionInfo.test.ts',
  'src/hooks/useJobQuestion.test.ts',
  'src/hooks/useWorkspaceEvents.test.ts',
  'src/lib/download.test.ts',
  'src/lib/htmlText.test.ts',
  'src/lib/latex.test.ts',
  'src/lib/sanitizeHtml.test.ts',
  'src/hooks/useJobFilterRefetch.test.ts',
  'src/pages/jobDetail/useUpgradeWorkflowAction.test.ts',
  'src/pages/workflowStudio/useWorkflowStudio.test.ts',
  'src/pages/workflowStudio/useWorkflowStudioActions.test.ts',
  'src/pages/workflowStudio/useWorkflowStudioMobilePanel.test.ts',
  'src/stores/agentsStore.test.ts',
  'src/stores/uiStore.test.ts',
]

// Dev-server proxy noise filter: when the browser reloads / HMR restarts, the
// proxy may still have frames in flight for a WebSocket the client already
// closed, and vite dumps a full "ws proxy error" stack trace for what is a
// benign disconnect (the client reconnects automatically). Collapse those to
// a one-line warning and keep every other error untouched.
const logger = createLogger()
const loggerError = logger.error
logger.error = (msg, options) => {
  const code = (options?.error as NodeJS.ErrnoException | undefined)?.code
  if (
    msg.includes('ws proxy error') &&
    (code === 'ERR_STREAM_WRITE_AFTER_END' || code === 'ECONNRESET')
  ) {
    logger.warn(`ws proxy: client disconnected (${code})`, { timestamp: true })
    return
  }
  loggerError(msg, options)
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react()],
    customLogger: logger,
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
            if (!id.includes('node_modules')) {
              return
            }
            // Split heavy route-scoped deps so they load only with the lazy
            // pages that import them (JobDetail / WorkflowStudio).
            if (id.includes('@mui') || id.includes('@emotion')) {
              return 'vendor-mui'
            }
            if (id.includes('@xyflow') || id.includes('dagre')) {
              return 'vendor-xyflow'
            }
            // Keep eager katex.min.css (imported in main.tsx) out of the
            // katex JS chunk, otherwise the entry preloads all of katex.
            if (id.includes('katex') && !id.endsWith('.css')) {
              return 'vendor-katex'
            }
            if (
              /node_modules\/(react|react-dom|react-router|react-router-dom|scheduler|@remix-run)\//.test(
                id
              )
            ) {
              return 'vendor-react'
            }
            // Markdown rendering (studio chat) and the JSON artifact viewer
            // are heavy and used by few routes — keep them out of the catch-all
            // vendor bucket, which the entry loads on first paint.
            if (id.includes('marked')) {
              return 'vendor-marked'
            }
            if (id.includes('react18-json-view')) {
              return 'vendor-json-view'
            }
            if (id.includes('uplot')) {
              return 'vendor-uplot'
            }
            if (id.includes('dompurify')) {
              return 'vendor-dompurify'
            }
            return 'vendor'
          },
        },
      },
    },
    test: {
      globals: true,
      exclude: commonTestExcludes,
      projects: [
        {
          extends: true,
          test: {
            name: 'logic',
            environment: 'node',
            include: ['src/**/*.test.ts'],
            exclude: [...commonTestExcludes, ...browserTestFiles],
            setupFiles: ['./src/test-setup-node.ts'],
          },
        },
        {
          extends: true,
          test: {
            name: 'component',
            environment: 'jsdom',
            include: ['src/**/*.test.tsx', ...browserTestFiles],
            exclude: commonTestExcludes,
            setupFiles: ['./src/test-setup.ts'],
          },
        },
      ],
      coverage: {
        provider: 'v8',
        include: ['src/**/*.{ts,tsx}'],
        reporter: ['text', 'json', 'html'],
        reportsDirectory: './coverage',
        reportOnFailure: true,
        thresholds: {
          lines: 86,
          functions: 80,
          branches: 72,
          statements: 82,
        },
        exclude: [
          'node_modules/',
          'src/**/*.d.ts',
          'src/generated/**',
          'src/testing/**',
          'src/test-setup*.ts',
          'src/**/*.test.ts',
          'src/**/*.test.tsx',
        ],
      },
    },
  }
})
