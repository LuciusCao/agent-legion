/**
 * PreviewPanelSection 回落路径与草稿预览的组件测试（issue #328）：
 * - 未定制 workspace（published=null）→ 渲染 fallback（现有通用预览）；
 * - 已发布 bundle → bundle host 接管，fallback 不再渲染；
 * - 「定制预览」对话期间左栏实时渲染草稿（仅当前用户可见）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { PreviewPanelSection } from './PreviewPanelSection'
import type { PreviewPanelState, PreviewPanelVersion } from './previewPanelApi'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { useAuthStore } from '../../stores/authStore'

const mockFetchPublished = vi.fn()
const mockFetchState = vi.fn()

vi.mock('./previewPanelApi', () => ({
  fetchPublishedPreviewPanel: (...args: unknown[]) =>
    mockFetchPublished(...args),
  fetchPreviewPanelState: (...args: unknown[]) => mockFetchState(...args),
}))

// 对话框本体（Studio chat 封装）在 CustomizePreviewDialog 自己的测试覆盖；
// 这里钉住的是 section 的组装与回落语义。
vi.mock('./CustomizePreviewDialog', () => ({
  CustomizePreviewDialog: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="customize-dialog">
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
    expect(iframe?.getAttribute('srcdoc')).toBe(PUBLISHED_HTML)
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

  it('定制对话期间左栏实时渲染草稿（仅当前用户可见）', async () => {
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
    ).toBe(PUBLISHED_HTML)

    fireEvent.click(screen.getByRole('button', { name: '定制预览' }))
    await waitFor(() =>
      expect(screen.getByTestId('customize-dialog')).toBeInTheDocument()
    )
    await waitFor(() => {
      const iframe = screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
      expect(iframe?.getAttribute('srcdoc')).toBe(DRAFT_HTML)
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
      expect(iframe?.getAttribute('srcdoc')).toBe(PUBLISHED_HTML)
    })
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
})
