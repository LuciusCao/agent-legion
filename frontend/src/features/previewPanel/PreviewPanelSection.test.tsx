/**
 * PreviewPanelSection 回落路径与草稿显式预览的组件测试（issue #328 / #347 P1）：
 * - 未定制 workspace（published=null）→ 渲染 fallback（现有通用预览）；
 * - 已发布 bundle → bundle host 接管，fallback 不再渲染；
 * - 「定制预览」对话期间草稿**不自动执行**（#347 P1）：左栏继续渲染已发布
 *   版本；显式点「预览此草稿」后才切换到草稿；关闭对话回到已发布版本，
 *   重开对话框回到默认态（不记忆执行态）。
 *
 * srcdoc 断言一律用「包含」：宿主会在 bundle 头部注入 CSP meta
 * （PreviewPanelHost 的出站网络红线），完整字符串不再等于 bundle 原文。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { PreviewPanelSection } from './PreviewPanelSection'
import type { PreviewPanelState, PreviewPanelVersion } from './previewPanelApi'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { useAuthStore } from '../../stores/authStore'
import { expectConsoleError, expectConsoleWarning } from '../../test-setup'

const mockFetchPublished = vi.fn()
const mockFetchState = vi.fn()

vi.mock('./previewPanelApi', () => ({
  fetchPublishedPreviewPanel: (...args: unknown[]) =>
    mockFetchPublished(...args),
  fetchPreviewPanelState: (...args: unknown[]) => mockFetchState(...args),
}))

// 对话框本体（Studio chat 封装）在 CustomizePreviewDialog 自己的测试覆盖；
// 这里钉住的是 section 的组装与回落语义。mock 透传显式预览动作（#347 P1），
// 供门控用例点击。
vi.mock('./CustomizePreviewDialog', () => ({
  CustomizePreviewDialog: ({
    onPreviewDraft,
    onClose,
  }: {
    onPreviewDraft: () => void
    onClose: () => void
  }) => (
    <div data-testid="customize-dialog">
      <button onClick={onPreviewDraft}>预览此草稿</button>
      <button onClick={onClose}>关闭</button>
    </div>
  ),
}))

function makeVersion(
  html: string,
  status: 'draft' | 'published'
): PreviewPanelVersion {
  return {
    id: `id-${status}`,
    workspace_id: 'ws1',
    entity_key: 'default',
    version: 1,
    status,
    html,
    html_hash: 'hash',
    created_by: 'studio-agent:u1',
    change_note: null,
    created_at: '2026-09-01T00:00:00Z',
    published_at: status === 'published' ? '2026-09-01T00:00:00Z' : null,
  }
}

const PUBLISHED_HTML =
  '<!doctype html><html><body>published panel</body></html>'
const DRAFT_HTML = '<!doctype html><html><body>draft panel</body></html>'

function renderSection(ui?: ReactElement) {
  return render(
    ui ?? (
      <PreviewPanelSection
        jobId="job-1"
        workspaceId="ws1"
        fallback={<div data-testid="generic-fallback">通用产物预览</div>}
      />
    ),
    { wrapper: TestQueryProvider }
  )
}

beforeEach(() => {
  mockFetchPublished.mockReset()
  mockFetchState.mockReset()
  mockFetchState.mockResolvedValue({
    published: null,
    draft: null,
  } satisfies PreviewPanelState)
  // 定制入口 admin-only（P4 惯例）：默认以 admin 身份渲染。
  act(() => {
    useAuthStore.setState({ user: { role: 'admin' } as never })
  })
})

afterEach(() => {
  act(() => {
    useAuthStore.setState({ user: null })
  })
})

describe('PreviewPanelSection', () => {
  it('未定制 workspace 回落现有通用预览', async () => {
    mockFetchPublished.mockResolvedValue(null)
    renderSection()

    await waitFor(() =>
      expect(screen.getByTestId('generic-fallback')).toBeInTheDocument()
    )
    expect(screen.queryByTestId('preview-panel-host')).toBeNull()
    // 「定制预览」入口在头部常驻
    expect(screen.getByRole('button', { name: '定制预览' })).toBeInTheDocument()
  })

  it('已发布 bundle 接管左栏，fallback 不再渲染', async () => {
    mockFetchPublished.mockResolvedValue(
      makeVersion(PUBLISHED_HTML, 'published')
    )
    renderSection()

    await waitFor(() =>
      expect(screen.getByTestId('preview-panel-host')).toBeInTheDocument()
    )
    const iframe = screen
      .getByTestId('preview-panel-host')
      .querySelector('iframe')
    expect(iframe?.getAttribute('srcdoc')).toContain('published panel')
    expect(screen.queryByTestId('generic-fallback')).toBeNull()
  })

  it('workspaceId 缺失时不渲染头部入口，直接回落', async () => {
    mockFetchPublished.mockResolvedValue(null)
    renderSection(
      <PreviewPanelSection
        jobId="job-1"
        fallback={<div data-testid="generic-fallback">通用产物预览</div>}
      />
    )

    await waitFor(() =>
      expect(screen.getByTestId('generic-fallback')).toBeInTheDocument()
    )
    expect(screen.queryByRole('button', { name: '定制预览' })).toBeNull()
  })

  it('定制对话期间草稿不自动执行：显式「预览此草稿」后执行，重开对话回到默认态（#347 P1）', async () => {
    mockFetchPublished.mockResolvedValue(
      makeVersion(PUBLISHED_HTML, 'published')
    )
    mockFetchState.mockResolvedValue({
      published: makeVersion(PUBLISHED_HTML, 'published'),
      draft: makeVersion(DRAFT_HTML, 'draft'),
    })
    renderSection()

    await waitFor(() =>
      expect(screen.getByTestId('preview-panel-host')).toBeInTheDocument()
    )
    expect(
      screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
        ?.getAttribute('srcdoc')
    ).toContain('published panel')

    // 打开定制对话：草稿已在治理面上可见，但左栏**不**自动切换到草稿——
    // 未审核 HTML 不得未经显式动作就作为 srcDoc 执行。
    fireEvent.click(screen.getByRole('button', { name: '定制预览' }))
    await waitFor(() =>
      expect(screen.getByTestId('customize-dialog')).toBeInTheDocument()
    )
    expect(
      screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
        ?.getAttribute('srcdoc')
    ).toContain('published panel')
    expect(screen.queryByText('草稿预览中')).toBeNull()

    // 显式动作后才执行草稿（仅当前用户可见）。
    fireEvent.click(screen.getByRole('button', { name: '预览此草稿' }))
    await waitFor(() => {
      const iframe = screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
      expect(iframe?.getAttribute('srcdoc')).toContain('draft panel')
    })
    expect(screen.getByText('草稿预览中')).toBeInTheDocument()

    // 关闭对话回到已发布版本
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    await waitFor(() =>
      expect(screen.queryByTestId('customize-dialog')).toBeNull()
    )
    await waitFor(() => {
      const iframe = screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
      expect(iframe?.getAttribute('srcdoc')).toContain('published panel')
    })

    // 重新打开对话框：回到默认态——一次点击不放行后续会话的草稿执行。
    fireEvent.click(screen.getByRole('button', { name: '定制预览' }))
    await waitFor(() =>
      expect(screen.getByTestId('customize-dialog')).toBeInTheDocument()
    )
    expect(
      screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
        ?.getAttribute('srcdoc')
    ).toContain('published panel')
    expect(screen.queryByText('草稿预览中')).toBeNull()
  })

  it('无已发布版本时草稿同样不自动执行：显式预览前渲染 fallback', async () => {
    mockFetchPublished.mockResolvedValue(null)
    mockFetchState.mockResolvedValue({
      published: null,
      draft: makeVersion(DRAFT_HTML, 'draft'),
    })
    renderSection()

    await waitFor(() =>
      expect(screen.getByTestId('generic-fallback')).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('button', { name: '定制预览' }))
    await waitFor(() =>
      expect(screen.getByTestId('customize-dialog')).toBeInTheDocument()
    )
    // 草稿存在但未显式预览：左栏保持 fallback，不挂草稿 iframe。
    await waitFor(() => expect(mockFetchState).toHaveBeenCalled())
    expect(screen.queryByTestId('preview-panel-host')).toBeNull()
    expect(screen.getByTestId('generic-fallback')).toBeInTheDocument()
    expect(screen.queryByText('草稿预览中')).toBeNull()

    // 显式动作后草稿接管左栏。
    fireEvent.click(screen.getByRole('button', { name: '预览此草稿' }))
    await waitFor(() => {
      const iframe = screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
      expect(iframe?.getAttribute('srcdoc')).toContain('draft panel')
    })
    expect(screen.getByText('草稿预览中')).toBeInTheDocument()
    expect(screen.queryByTestId('generic-fallback')).toBeNull()
  })

  it('预览中草稿经 null 过渡消失后，同会话新草稿不继承旧授权自动执行（review P1）', async () => {
    // 草稿 null→v2 的过渡要经 3s 轮询送达，走 fake timers（同 codex P2
    // 用例的 known noise 声明）。
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    vi.useFakeTimers()
    try {
      mockFetchPublished.mockResolvedValue(
        makeVersion(PUBLISHED_HTML, 'published')
      )
      // 首轮：草稿 v1 就位。
      mockFetchState.mockResolvedValue({
        published: makeVersion(PUBLISHED_HTML, 'published'),
        draft: makeVersion(DRAFT_HTML, 'draft'),
      } satisfies PreviewPanelState)
      renderSection()
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })

      fireEvent.click(screen.getByRole('button', { name: '定制预览' }))
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })
      // 显式预览 v1。
      fireEvent.click(screen.getByRole('button', { name: '预览此草稿' }))
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })
      expect(
        screen
          .getByTestId('preview-panel-host')
          .querySelector('iframe')
          ?.getAttribute('srcdoc')
      ).toContain('draft panel')

      // 发布草稿（对话框不关）：draft 变 null，左栏回落已发布版本，
      // 按钮回到「预览此草稿」——授权已失效，不能悬空成「预览草稿中」。
      mockFetchState.mockResolvedValue({
        published: makeVersion(PUBLISHED_HTML, 'published'),
        draft: null,
      } satisfies PreviewPanelState)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3100)
      })
      expect(
        screen
          .getByTestId('preview-panel-host')
          .querySelector('iframe')
          ?.getAttribute('srcdoc')
      ).toContain('published panel')
      expect(screen.getByRole('button', { name: '预览此草稿' })).toBeEnabled()

      // 同一 chat 会话里 agent 写入新草稿 v2（「发布后继续改一版」的核心
      // 工作流）：v2 必须重新显式预览，不得继承 v1 的授权自动执行。
      mockFetchState.mockResolvedValue({
        published: makeVersion(PUBLISHED_HTML, 'published'),
        draft: makeVersion(
          '<!doctype html><html><body>draft v2 panel</body></html>',
          'draft'
        ),
      } satisfies PreviewPanelState)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3100)
      })
      expect(
        screen
          .getByTestId('preview-panel-host')
          .querySelector('iframe')
          ?.getAttribute('srcdoc')
      ).toContain('published panel')
      expect(screen.queryByText('草稿预览中')).toBeNull()

      // 再次显式预览才执行 v2。
      fireEvent.click(screen.getByRole('button', { name: '预览此草稿' }))
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })
      expect(
        screen
          .getByTestId('preview-panel-host')
          .querySelector('iframe')
          ?.getAttribute('srcdoc')
      ).toContain('draft v2 panel')
    } finally {
      vi.useRealTimers()
    }
  })

  it('预览中归档（恢复默认）后回落 fallback（review P2）', async () => {
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    vi.useFakeTimers()
    try {
      mockFetchPublished.mockResolvedValue(null)
      mockFetchState.mockResolvedValue({
        published: null,
        draft: makeVersion(DRAFT_HTML, 'draft'),
      } satisfies PreviewPanelState)
      renderSection()
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })

      fireEvent.click(screen.getByRole('button', { name: '定制预览' }))
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })
      fireEvent.click(screen.getByRole('button', { name: '预览此草稿' }))
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })
      expect(
        screen
          .getByTestId('preview-panel-host')
          .querySelector('iframe')
          ?.getAttribute('srcdoc')
      ).toContain('draft panel')
      expect(screen.getByText('草稿预览中')).toBeInTheDocument()

      // 恢复默认（归档）：draft 变 null 且无已发布版本 → 回落 fallback，
      // 草稿 iframe 卸载、徽标消失（授权随 null 过渡失效）。
      mockFetchPublished.mockResolvedValue(null)
      mockFetchState.mockResolvedValue({
        published: null,
        draft: null,
      } satisfies PreviewPanelState)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3100)
      })
      expect(screen.getByTestId('generic-fallback')).toBeInTheDocument()
      expect(screen.queryByTestId('preview-panel-host')).toBeNull()
      expect(screen.queryByText('草稿预览中')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('非 admin 成员不渲染「定制预览」入口（P4 惯例，治理面端点对其 403）', async () => {
    act(() => {
      useAuthStore.setState({ user: { role: 'member' } as never })
    })
    mockFetchPublished.mockResolvedValue(
      makeVersion(PUBLISHED_HTML, 'published')
    )
    renderSection()

    // 面板内容对成员照常渲染，但定制入口与治理面查询都不出现。
    await waitFor(() =>
      expect(screen.getByTestId('preview-panel-host')).toBeInTheDocument()
    )
    expect(screen.queryByRole('button', { name: '定制预览' })).toBeNull()
    expect(mockFetchState).not.toHaveBeenCalled()
  })

  it('bundle 内容变化时重挂 iframe（旧文档在途桥请求的响应无处可投，codex P2）', async () => {
    // react-query 的 refetch 落在 fake-timer 区间外时，查询解析会脱离
    // act 包裹（known noise），声明预期以聚焦本用例的断言。
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    vi.useFakeTimers()
    try {
      mockFetchPublished.mockResolvedValue(
        makeVersion(PUBLISHED_HTML, 'published')
      )
      // 首轮 state：草稿 v1（bundle-v1）就位。
      mockFetchState.mockResolvedValue({
        published: makeVersion(PUBLISHED_HTML, 'published'),
        draft: makeVersion(DRAFT_HTML, 'draft'),
      } satisfies PreviewPanelState)
      renderSection()
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })
      // 打开定制对话启用草稿轮询（3s refetchInterval）。草稿不自动执行
      // （#347 P1）：显式预览后左栏才切到草稿 v1 渲染。
      fireEvent.click(screen.getByRole('button', { name: '定制预览' }))
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })
      fireEvent.click(screen.getByRole('button', { name: '预览此草稿' }))
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })
      const firstFrame = screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
      expect(firstFrame?.getAttribute('srcdoc')).toContain('draft panel')

      // 轮询推进：agent 保存了新草稿（bundle 内容更新，draft 持续非
      // null——save_draft 覆盖同一草稿，授权保持）。key 含 bundle 内容
      // → iframe 元素必须被替换——沿用同一 contentWindow 做 srcDoc 导航
      // 会让旧文档在途请求的响应错误应答新文档的同编号请求。
      mockFetchState.mockResolvedValue({
        published: makeVersion(PUBLISHED_HTML, 'published'),
        draft: makeVersion(
          '<!doctype html><html><body>draft v2 panel</body></html>',
          'draft'
        ),
      } satisfies PreviewPanelState)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3100)
      })

      const secondFrame = screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
      expect(secondFrame?.getAttribute('srcdoc')).toContain('draft v2 panel')
      expect(secondFrame).not.toBe(firstFrame)
    } finally {
      vi.useRealTimers()
    }
  })
})
