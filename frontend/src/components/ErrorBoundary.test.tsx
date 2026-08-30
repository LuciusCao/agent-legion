import {
  describe,
  it,
  expect,
  vi,
  beforeAll,
  beforeEach,
  afterEach,
} from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { ErrorBoundary, isChunkLoadError } from './ErrorBoundary'
import { ErrorFallback } from './ErrorFallback'
import WorkspaceLayout from '../layouts/WorkspaceLayout'
import { expectConsoleError } from '../test-setup'
import { createMockAgentsState, createMockUiState } from '../testing/fixtures'

const fetchWorkerStatusMock = vi.fn()

vi.mock('../stores/agentsStore', () => ({
  useAgentsStore: (
    selector?: (state: ReturnType<typeof createMockAgentsState>) => unknown
  ) => {
    const state = createMockAgentsState({
      fetchWorkerStatus: fetchWorkerStatusMock,
    })
    return selector ? selector(state) : state
  },
}))

vi.mock('../stores/uiStore', () => ({
  useUiStore: (
    selector?: (state: ReturnType<typeof createMockUiState>) => unknown
  ) => {
    const state = createMockUiState()
    return selector ? selector(state) : state
  },
}))

vi.mock('../api', () => ({
  fetchWorkspaces: vi.fn().mockResolvedValue({
    workspaces: [
      {
        id: 'ws1',
        name: '测试空间',
        default_workflow_key: 'question_content',
        default_entity: 'question',
      },
    ],
  }),
}))

function Boom(): JSX.Element {
  throw new Error('页面渲染失败')
}

function ChunkBoom(): JSX.Element {
  // Chrome 动态 import 失败的典型消息（发版后旧 chunk hash 404）。
  throw new TypeError(
    'Failed to fetch dynamically imported module: http://localhost/assets/JobDetailPage-D3fA1b.js'
  )
}

/** jsdom 的 window.location.reload 不可 spyOn，用可配置属性替换整个 location。 */
function mockLocationReload(): ReturnType<typeof vi.fn> {
  const reload = vi.fn()
  Object.defineProperty(globalThis, 'location', {
    value: { ...globalThis.location, reload },
    configurable: true,
    writable: true,
  })
  return reload
}

/** render 一个会抛错的子树的便捷封装：抑制 window error 噪音（用例内不清理）。 */
function renderCrash(ui: JSX.Element) {
  const prevent = (event: ErrorEvent) => event.preventDefault()
  window.addEventListener('error', prevent)
  const view = render(ui)
  return () => {
    window.removeEventListener('error', prevent)
    view.unmount()
  }
}

describe('ErrorBoundary fallback 渲染', () => {
  beforeEach(() => {
    expectConsoleError(/页面渲染失败/)
    expectConsoleError(/The above error occurred/)
  })

  it('捕获子组件渲染错误并展示错误 UI', () => {
    renderCrash(
      <ErrorBoundary
        fallback={
          <ErrorFallback
            title="页面出错了"
            description="当前页面渲染发生异常"
          />
        }
      >
        <Boom />
      </ErrorBoundary>
    )

    expect(screen.getByRole('alert')).toHaveTextContent('页面出错了')
    expect(screen.getByText('当前页面渲染发生异常')).toBeInTheDocument()
  })

  it('错误消除后上层重建 boundary 可恢复子树（key 重置触发局部 remount）', () => {
    let shouldThrow = true
    function Flaky() {
      if (shouldThrow) throw new Error('页面渲染失败')
      return <div>恢复的内容</div>
    }
    function Harness({ resetKey }: { resetKey: string }) {
      return (
        <ErrorBoundary
          key={resetKey}
          fallback={
            <ErrorFallback
              title="页面出错了"
              description="当前页面渲染发生异常"
              onRetry={() => {
                shouldThrow = false
              }}
            />
          }
        >
          <Flaky />
        </ErrorBoundary>
      )
    }

    const first = render(<Harness resetKey="first" />)
    expect(screen.getByRole('alert')).toBeInTheDocument()

    // 用户点击「重试」：先修复错误源（这里模拟数据恢复），再由上层 key 变化
    // 整体重建 boundary（WorkspaceLayout 的 pageKey 递增即此机制）。
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    first.unmount()

    render(<Harness resetKey="second" />)
    expect(screen.getByText('恢复的内容')).toBeInTheDocument()
  })
})

describe('chunk 失败识别', () => {
  it.each([
    [
      'Chrome 动态 import 失败',
      new TypeError(
        'Failed to fetch dynamically imported module: http://x/y.js'
      ),
      true,
    ],
    [
      'Firefox 动态 import 失败',
      new TypeError('error loading dynamically imported module: http://x/y.js'),
      true,
    ],
    [
      'Safari 模块脚本失败',
      new TypeError('Importing a module script failed.'),
      true,
    ],
    [
      'Vite preload 依赖失败',
      new TypeError('Unable to preload dependency'),
      true,
    ],
    [
      'webpack ChunkLoadError',
      Object.assign(new Error('Loading chunk 42 failed.'), {
        name: 'ChunkLoadError',
      }),
      true,
    ],
    ['普通渲染错误', new Error('页面渲染失败'), false],
    ['非 Error 值', '字符串错误', false],
  ])('识别 %s', (_label, error, expected) => {
    expect(isChunkLoadError(error)).toBe(expected)
  })
})

describe('chunk 失败整页 reload 一次（带退出条件）', () => {
  let reload: ReturnType<typeof vi.fn>

  beforeAll(() => {
    reload = mockLocationReload()
  })

  beforeEach(() => {
    reload.mockClear()
    window.sessionStorage.clear()
    expectConsoleError(/Failed to fetch dynamically imported module/)
    expectConsoleError(/The above error occurred/)
  })

  afterEach(() => {
    window.sessionStorage.clear()
  })

  it('首次 chunk 失败触发一次整页 reload 并写入 sessionStorage 标记', () => {
    renderCrash(
      <ErrorBoundary fallback={<div>错误页</div>} reloadOnChunkError>
        <ChunkBoom />
      </ErrorBoundary>
    )

    expect(reload).toHaveBeenCalledTimes(1)
    expect(window.sessionStorage.getItem('agent-legion:chunk-reloaded')).toBe(
      '1'
    )
    expect(screen.getByText('错误页')).toBeInTheDocument()
  })

  it('已 reload 过（标记存在）则不再 reload，降级为错误页避免循环', () => {
    window.sessionStorage.setItem('agent-legion:chunk-reloaded', '1')

    renderCrash(
      <ErrorBoundary fallback={<div>降级错误页</div>} reloadOnChunkError>
        <ChunkBoom />
      </ErrorBoundary>
    )

    expect(reload).not.toHaveBeenCalled()
    expect(screen.getByText('降级错误页')).toBeInTheDocument()
  })

  it('未开启 reloadOnChunkError 时不 reload（页面级边界只做局部隔离）', () => {
    renderCrash(
      <ErrorBoundary fallback={<div>局部错误页</div>}>
        <ChunkBoom />
      </ErrorBoundary>
    )

    expect(reload).not.toHaveBeenCalled()
    expect(window.sessionStorage.getItem('agent-legion:chunk-reloaded')).toBe(
      null
    )
  })

  it('普通渲染错误不触发 reload', () => {
    expectConsoleError(/页面渲染失败/)
    expectConsoleError(/The above error occurred/)
    renderCrash(
      <ErrorBoundary fallback={<div>错误页</div>} reloadOnChunkError>
        <Boom />
      </ErrorBoundary>
    )

    expect(reload).not.toHaveBeenCalled()
  })
})

describe('WorkspaceLayout 页面级隔离', () => {
  beforeAll(() => {
    mockLocationReload()
  })

  beforeEach(() => {
    fetchWorkerStatusMock.mockClear()
    fetchWorkerStatusMock.mockResolvedValue(undefined)
    window.sessionStorage.clear()
    expectConsoleError(/工作区页面渲染失败/)
    expectConsoleError(/The above error occurred/)
  })

  it('子页面崩溃时 shell（AppBar / 导航）仍可用', () => {
    renderCrash(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          >
            <Route index element={<WorkspaceCrashPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    )

    // 崩溃页面被局部边界捕获，渲染错误 UI。
    expect(screen.getByRole('alert')).toHaveTextContent('页面出错了')
    // shell 仍在：AppBar 的设置按钮可点（不崩溃、可导航离开当前页）。
    const settingsButton = screen.getByLabelText('设置')
    expect(settingsButton).toBeInTheDocument()
    fireEvent.click(settingsButton)
  })

  it('点击重试后错误 UI 仍渲染（局部 remount 由 key 驱动）', () => {
    renderCrash(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          >
            <Route index element={<WorkspaceCrashPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    )

    const retryButton = screen.getByRole('button', { name: '重试' })
    expect(retryButton).toBeInTheDocument()
    fireEvent.click(retryButton)
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  function WorkspaceCrashPage(): JSX.Element {
    throw new Error('工作区页面渲染失败')
  }
})
