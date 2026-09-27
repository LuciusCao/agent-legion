/**
 * PreviewPanelSection 的 #528 预览模式开关测试（姊妹文件——
 * PreviewPanelSection.test.tsx 已近 800 行纪律线，按被测主题拆分）：
 * workspace 有已发布定制面板时头部出现「定制面板 | 原始界面」开关——
 * 默认定制面板；切原始界面回落 fallback；切回恢复；偏好按 workspace 存
 * localStorage（重挂载跟随存储）；非 admin 可用；draftPreview 草稿预览态
 * 不受开关影响；无已发布 bundle 时不渲染开关；react-router 复用实例跨
 * workspace 导航时不把 ws1 的会话内覆盖串到 ws2。
 * fixture 与 mock 形状同 PreviewPanelSection.test.tsx。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { createHash } from 'node:crypto'
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
  publishPreviewPanel: vi.fn(),
  archivePreviewPanel: vi.fn(),
}))

// 该 jsdom 环境不提供 localStorage：用内存 stub（同
// PreviewPanelSection.test.tsx 的模式）。
function installLocalStorageStub() {
  const store = new Map<string, string>()
  const stub: Storage = {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (key) => store.get(key) ?? null,
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => void store.delete(key),
    setItem: (key, value) => void store.set(key, String(value)),
  }
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: stub,
  })
  return stub
}

const localStorageStub = installLocalStorageStub()

// Dock 本体在 CustomizePreviewDock 自己的测试覆盖；这里只需要「关闭」出口。
vi.mock('./CustomizePreviewDock', () => ({
  CustomizePreviewDock: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="customize-dialog">
      <button onClick={onClose}>关闭</button>
    </div>
  ),
}))

function sha256Hex(html: string): string {
  return createHash('sha256').update(html, 'utf8').digest('hex')
}

function makeBundleSync(
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
    html_hash: sha256Hex(html),
    created_by: 'studio-agent:u1',
    change_note: null,
    created_at: '2026-09-01T00:00:00Z',
    published_at: status === 'published' ? '2026-09-01T00:00:00Z' : null,
  }
}

const PUBLISHED = makeBundleSync(
  '<!doctype html><html><body>published panel</body></html>',
  'published'
)
const DRAFT = makeBundleSync(
  '<!doctype html><html><body>draft panel</body></html>',
  'draft'
)

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

function modeToggle() {
  return screen.getByRole('group', { name: '预览显示模式' })
}

beforeEach(() => {
  mockFetchPublished.mockReset()
  mockFetchState.mockReset()
  mockFetchState.mockResolvedValue({
    published: null,
    draft: null,
  } satisfies PreviewPanelState)
  localStorageStub.clear()
  act(() => {
    useAuthStore.setState({ user: { role: 'admin' } as never })
  })
})

afterEach(() => {
  act(() => {
    useAuthStore.setState({ user: null })
  })
})

describe('PreviewPanelSection #528 预览模式开关', () => {
  it('有已发布 bundle 时默认渲染定制面板；切「原始界面」回落 fallback，切回恢复', async () => {
    // host 重挂的 jsdom load 脱离 act（known noise，同主文件用例的声明）。
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    mockFetchPublished.mockResolvedValue(PUBLISHED)
    renderSection()

    // 默认（无存储偏好）= 定制面板：host 接管，开关存在且「定制面板」激活。
    await waitFor(() =>
      expect(screen.getByTestId('preview-panel-host')).toBeInTheDocument()
    )
    expect(screen.getByRole('button', { name: '定制面板' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(screen.queryByTestId('generic-fallback')).toBeNull()

    // 切「原始界面」：回落 fallback，定制 host 卸载——「暂时不看了」，
    // 不是归档（published 数据不变）。
    fireEvent.click(screen.getByRole('button', { name: '原始界面' }))
    await waitFor(() =>
      expect(screen.getByTestId('generic-fallback')).toBeInTheDocument()
    )
    expect(screen.queryByTestId('preview-panel-host')).toBeNull()
    expect(mockFetchPublished).toHaveBeenCalled()

    // 切回「定制面板」：host 恢复，job 上下文不变。
    fireEvent.click(modeToggle().querySelector('button') as HTMLElement)
    await waitFor(() =>
      expect(screen.getByTestId('preview-panel-host')).toBeInTheDocument()
    )
    const iframe = screen
      .getByTestId('preview-panel-host')
      .querySelector('iframe')
    expect(iframe?.getAttribute('srcdoc')).toContain('published panel')
  })

  it('偏好按 workspace 存 localStorage：重挂载跟随存储（原始界面记忆）', async () => {
    mockFetchPublished.mockResolvedValue(PUBLISHED)
    const first = renderSection()
    await waitFor(() =>
      expect(screen.getByTestId('preview-panel-host')).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('button', { name: '原始界面' }))
    await waitFor(() =>
      expect(screen.getByTestId('generic-fallback')).toBeInTheDocument()
    )
    first.unmount()

    // 重挂载（新会话等价物）：直接回落 fallback——记忆生效。
    renderSection()
    await waitFor(() =>
      expect(screen.getByTestId('generic-fallback')).toBeInTheDocument()
    )
    expect(screen.queryByTestId('preview-panel-host')).toBeNull()
  })

  it('非 admin 成员同样可切换（查看偏好非治理动作），且无定制入口/治理行', async () => {
    act(() => {
      useAuthStore.setState({ user: { role: 'member' } as never })
    })
    mockFetchPublished.mockResolvedValue(PUBLISHED)
    renderSection()

    await waitFor(() =>
      expect(screen.getByTestId('preview-panel-host')).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('button', { name: '原始界面' }))
    await waitFor(() =>
      expect(screen.getByTestId('generic-fallback')).toBeInTheDocument()
    )
    expect(screen.queryByTestId('preview-panel-host')).toBeNull()
    expect(screen.queryByRole('button', { name: '定制预览' })).toBeNull()
    expect(screen.queryByRole('button', { name: '预览治理操作' })).toBeNull()
    expect(mockFetchState).not.toHaveBeenCalled()
  })

  it('draftPreview 草稿预览态不受开关影响：mode=original 时授权草稿仍渲染草稿', async () => {
    // host 重挂的 jsdom load 脱离 act（known noise）。
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    window.localStorage.setItem('preview-panel-display-mode:ws1', 'original')
    mockFetchPublished.mockResolvedValue(PUBLISHED)
    mockFetchState.mockResolvedValue({
      published: PUBLISHED,
      draft: DRAFT,
    } satisfies PreviewPanelState)
    renderSection()

    // 存储偏好 original：常态回落 fallback。
    await waitFor(() =>
      expect(screen.getByTestId('generic-fallback')).toBeInTheDocument()
    )
    // admin 逐次授权「预览此草稿」（#796 R4 起外露出头部治理区）：
    // 草稿预览优先级高于开关——左栏渲染草稿。
    await screen.findByText(/草稿 v1 · /)
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

  it('无已发布 bundle 时不渲染开关（避免噪音），草稿授权路径不受影响', async () => {
    mockFetchPublished.mockResolvedValue(null)
    renderSection()
    await waitFor(() =>
      expect(screen.getByTestId('generic-fallback')).toBeInTheDocument()
    )
    expect(screen.queryByRole('group', { name: '预览显示模式' })).toBeNull()
  })

  it('react-router 复用实例跨 workspace 导航：ws1 的会话内覆盖不串到 ws2', async () => {
    mockFetchPublished.mockResolvedValue(PUBLISHED)
    const { rerender } = renderSection()
    await waitFor(() =>
      expect(screen.getByTestId('preview-panel-host')).toBeInTheDocument()
    )
    // ws1 切到原始界面（会话内覆盖 + 写 ws1 存储）。
    fireEvent.click(screen.getByRole('button', { name: '原始界面' }))
    await waitFor(() =>
      expect(screen.getByTestId('generic-fallback')).toBeInTheDocument()
    )

    // 同实例导航到 ws2（无存储偏好）：必须读 ws2 自己的偏好（默认定制
    // 面板），不能沿用 ws1 的覆盖。
    rerender(
      <PreviewPanelSection
        jobId="job-2"
        workspaceId="ws2"
        fallback={<div data-testid="generic-fallback">通用产物预览</div>}
      />
    )
    await waitFor(() =>
      expect(screen.getByTestId('preview-panel-host')).toBeInTheDocument()
    )
    expect(window.localStorage.getItem('preview-panel-display-mode:ws1')).toBe(
      'original'
    )
    expect(
      window.localStorage.getItem('preview-panel-display-mode:ws2')
    ).toBeNull()
  })
})
