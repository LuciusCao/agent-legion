import { Component, type ErrorInfo, type ReactNode } from 'react'

/**
 * 动态 import() 失败（发版后旧 chunk hash 失效 / 弱网）的错误特征（#271）。
 *
 * 识别依据：ES Module 规范没有为 import 失败定义标准 error.name，各内核抛的
 * 都是 TypeError 且消息文本各异（React.lazy 会把 rejected promise 的错误原样
 * 抛给上层边界，因此懒加载页面失败会走到这里）：
 * - Chrome:  `Failed to fetch dynamically imported module: <url>`
 * - Firefox: `error loading dynamically imported module: <url>`
 * - Safari:  `Importing a module script failed.`
 * - Vite 的 modulePreload polyfill 把 preload 失败包装为
 *   `Unable to preload dependency` / `error during module preloading` /
 *   `Preload error`（vite:preloadError 事件本身不冒泡，import() 侧仍以消息形态抛出）
 * - webpack 生态的历史形态（error.name = ChunkLoadError /
 *   `Loading chunk <n> failed`）留作宽松兜底。
 */
const CHUNK_LOAD_ERROR_PATTERNS: RegExp[] = [
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /importing a module script failed/i,
  /unable to preload dependency/i,
  /error during module preloading/i,
  /preload error/i,
  /loading chunk .+ failed/i,
]

/** chunk 失败已触发过一次整页 reload 的 sessionStorage 标记（退出条件，防循环）。 */
const CHUNK_RELOADED_KEY = 'agent-legion:chunk-reloaded'

export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  // webpack 产物会赋 error.name = ChunkLoadError；Vite 产物无 name，靠消息匹配。
  if (error.name === 'ChunkLoadError') return true
  return CHUNK_LOAD_ERROR_PATTERNS.some((pattern) =>
    pattern.test(error.message)
  )
}

function hasReloadedForChunkError(): boolean {
  try {
    return window.sessionStorage.getItem(CHUNK_RELOADED_KEY) === '1'
  } catch {
    // sessionStorage 不可用（隐私模式等）时按「未 reload 过」处理。
    return false
  }
}

function markReloadedForChunkError() {
  try {
    window.sessionStorage.setItem(CHUNK_RELOADED_KEY, '1')
  } catch {
    // 写不进去则本次会话内退化为最多再 reload 一次，可接受。
  }
}

export interface ErrorBoundaryProps {
  /** 被包裹的子树；子树渲染抛错时切换到 fallback。 */
  children?: ReactNode
  /** 捕获到渲染错误时展示的兜底 UI；不传则渲染 null（静默隔离）。 */
  fallback?: ReactNode
  /**
   * 动态 import chunk 失败时的自愈：整页 reload 一次拿新 index.html 引用的
   * 新 hash chunk。同一浏览器会话内只 reload 一次（sessionStorage 标记是
   * 退出条件）；已 reload 过仍失败则降级为 fallback 错误页，避免死循环。
   */
  reloadOnChunkError?: boolean
}

interface ErrorBoundaryState {
  hasError: boolean
}

/**
 * class 组件错误边界：getDerivedStateFromError 切换到 fallback，
 * componentDidCatch（commit 阶段回调，fallback DOM 已提交）做上报与
 * chunk 自愈副作用。App 层（Suspense 外）与 WorkspaceLayout 层（Outlet 外）
 * 各放一个，后者把崩溃隔离在页面区域内，AppBar / 导航 shell 保持可用。
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    // 错误上报锚点：后续接 Sentry（issue #273 可观测性）时在此改为
    // Sentry.captureException(error, {
    //   contexts: { react: { componentStack: info.componentStack } },
    // })。现阶段先保证错误可见且不打断降级渲染。
    console.error('[ErrorBoundary] 渲染错误', error, info.componentStack)

    // chunk 加载失败无法靠局部 remount 自愈（React.lazy 会缓存 rejected 的
    // import promise，重试只会重抛同一个错误），唯一恢复路径是整页 reload
    // 拿新 index.html；sessionStorage 标记保证每个会话最多 reload 一次。
    if (
      this.props.reloadOnChunkError &&
      isChunkLoadError(error) &&
      !hasReloadedForChunkError()
    ) {
      markReloadedForChunkError()
      window.location.reload()
    }
  }

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children
    return this.props.fallback ?? null
  }
}
