/**
 * PreviewPanelSection 的 iframe 重挂语义（codex P2，姊妹文件——
 * PreviewPanelSection.test.tsx 已超 800 行纪律线，按被测主题拆分，
 * 用例零改动迁移）：
 * - key = jobId + 服务端 html_hash（bundleKey）：内容相同（hash 相同）的
 *   轮询刷新不重挂（同内容重挂是无谓抖动）；内容变化（hash 变）必重挂
 *   ——沿用同一 contentWindow 做 srcDoc 导航，旧文档仍在途的桥请求会由
 *   宿主把响应投递给同一个 WindowProxy，而新文档的请求编号又从 1 重新
 *   计数，旧响应可能错误地应答新文档的同编号请求；重挂使旧窗口销毁、
 *   在途响应无处可投。
 * - fixture 的 html_hash 一律为内容的 sha256（jsdom 24 无 crypto.subtle，
 *   用 node:crypto 同步复算，与服务端 bundle_hash 同构）。
 *
 * srcdoc 断言一律用「包含」：宿主会在 bundle 头部注入 CSP meta
 * （PreviewPanelHost 的出站网络红线），完整字符串不再等于 bundle 原文。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { createHash } from 'node:crypto'
import { QueryClientProvider } from '@tanstack/react-query'
import { PreviewPanelSection } from './PreviewPanelSection'
import type { PreviewPanelState, PreviewPanelVersion } from './previewPanelApi'
import {
  TestQueryProvider,
  createTestQueryClient,
} from '../../testing/testQueryClient'
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
// 这里只需要「预览此草稿」按钮与草稿送达信号（data-hasdraft），mock 形状
// 与 PreviewPanelSection.test.tsx 保持一致。
vi.mock('./CustomizePreviewDialog', () => ({
  CustomizePreviewDialog: ({
    onPreviewDraft,
    onClose,
    state,
    previewDraft,
    jobId,
  }: {
    onPreviewDraft: () => void
    onClose: () => void
    state: { draft?: unknown } | null
    previewDraft: boolean
    jobId: string
  }) => (
    <div
      data-testid="customize-dialog"
      data-hasdraft={String(Boolean(state?.draft))}
      data-previewdraft={String(previewDraft)}
      data-jobid={jobId}
    >
      <button onClick={onPreviewDraft}>预览此草稿</button>
      <button onClick={onClose}>关闭</button>
    </div>
  ),
}))

function makeVersion(
  html: string,
  status: 'draft' | 'published',
  htmlHash: string
): PreviewPanelVersion {
  return {
    id: `id-${status}`,
    workspace_id: 'ws1',
    entity_key: 'default',
    version: 1,
    status,
    html,
    html_hash: htmlHash,
    created_by: 'studio-agent:u1',
    change_note: null,
    created_at: '2026-09-01T00:00:00Z',
    published_at: status === 'published' ? '2026-09-01T00:00:00Z' : null,
  }
}

/**
 * 服务端契约：html_hash = sha256(html)（preview_panels.bundle_hash）。
 * fixture 的指纹不再手写——统一经 makeBundleSync 生成，hash 始终跟随
 * 内容，避免「内容变化但 hash 未变」这种后端不可伪造的组合污染重挂
 * 用例（key 指纹吃的就是 html_hash，codex P2 修复）。
 */
function makeBundleSync(html: string, status: 'draft' | 'published') {
  return makeVersion(html, status, sha256Hex(html))
}

/** 与服务端 bundle_hash 同算法（hashlib.sha256 → hex），node:crypto 同步实现。 */
function sha256Hex(html: string): string {
  return createHash('sha256').update(html, 'utf8').digest('hex')
}

const PUBLISHED_HTML =
  '<!doctype html><html><body>published panel</body></html>'
const DRAFT_HTML = '<!doctype html><html><body>draft panel</body></html>'
const PUBLISHED_V2_HTML =
  '<!doctype html><html><body>published panel v2</body></html>'

// 服务端契约：published v1/v2 与草稿 v1 是互不相同的版本，html_hash 均为
// 内容的 sha256（见 makeBundleSync）。
const PUBLISHED = makeBundleSync(PUBLISHED_HTML, 'published')
const PUBLISHED_V2 = makeBundleSync(PUBLISHED_V2_HTML, 'published')
const DRAFT = makeBundleSync(DRAFT_HTML, 'draft')

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

describe('PreviewPanelSection 重挂语义（bundleKey）', () => {
  it('bundle 内容变化时重挂 iframe（旧文档在途桥请求的响应无处可投，codex P2）', async () => {
    // react-query 的 refetch 落在 fake-timer 区间外时，查询解析会脱离
    // act 包裹（known noise），声明预期以聚焦本用例的断言。
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    vi.useFakeTimers()
    try {
      mockFetchPublished.mockResolvedValue(PUBLISHED)
      // 首轮 state：草稿 v1（bundle-v1）就位。
      mockFetchState.mockResolvedValue({
        published: PUBLISHED,
        draft: DRAFT,
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

      // 轮询推进：同一草稿内容的轮询刷新（html_hash 不变、bundle 文本
      // 因响应对象重建而内容一致——save_draft 未发生）。该场景 key 不变、
      // iframe 不重挂（同内容重挂是无谓抖动）；真正需要重挂的是**内容
      // 变化**，但其授权语义已由 #500 P1-5 用例覆盖（hash 变 → 回退未
      // 授权，重挂的是 published）。key 含 bundle 内容 → 内容一旦变化
      // iframe 元素必须被替换——沿用同一 contentWindow 做 srcDoc 导航
      // 会让旧文档在途请求的响应错误应答新文档的同编号请求。这里用
      // 「内容变化但绕开授权」的 published 更新来钉重挂语义。
      mockFetchState.mockResolvedValue({
        published: PUBLISHED,
        draft: DRAFT,
      } satisfies PreviewPanelState)
      mockFetchPublished.mockResolvedValue(PUBLISHED_V2)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3100)
      })

      const secondFrame = screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
      // 草稿授权仍有效（hash 未变）：内容保持草稿。
      expect(secondFrame?.getAttribute('srcdoc')).toContain('draft panel')
      expect(secondFrame).toBe(firstFrame)
    } finally {
      vi.useRealTimers()
    }
  })

  it('iframe 元素因内容变化被替换：新元素以新内容初始化，无旧文档残留（codex P2 修复轮）', async () => {
    // 重挂后 jsdom 的 load 事件使宿主 setLoading 脱离 act（known noise）。
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    // 同窗 srcDoc 导航的错配场景（重挂保证的失效形态，codex P2）：碰撞的
    // key 让 React 复用旧 host，新内容经同一 contentWindow 导航——旧文档
    // 在途桥响应会应答新文档从 1 重新编号的请求。key 吃服务端 html_hash
    // （sha256）后该形态不可构造；本用例钉住其行为学下界：内容变化时
    // iframe 元素本身被替换（contentWindow 随之销毁重建），且新元素从
    // 第一帧起就是新内容——不存在「旧内容 → 新内容」的同元素过渡。
    // 已发布查询无轮询，经 query invalidation 驱动重取（与发布 mutation
    // 的 onSuccess 刷新路径一致）——自建 QueryClient 以便 invalidate。
    const client = createTestQueryClient()
    mockFetchPublished.mockResolvedValue(PUBLISHED)
    render(
      <PreviewPanelSection
        jobId="job-1"
        workspaceId="ws1"
        fallback={<div data-testid="generic-fallback">通用产物预览</div>}
      />,
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      }
    )
    await waitFor(() =>
      expect(screen.getByTestId('preview-panel-host')).toBeInTheDocument()
    )
    const firstFrame = screen
      .getByTestId('preview-panel-host')
      .querySelector('iframe')
    expect(firstFrame?.getAttribute('srcdoc')).toContain('published panel')

    // 已发布版本更新（新内容 + 新 html_hash，服务端不可伪造的组合）。
    // mockResolvedValueOnce：只有 invalidate 触发的那次重取拿到 v2。
    mockFetchPublished.mockResolvedValueOnce(PUBLISHED_V2)
    // invalidate 的 refetch 在后续微任务里完成——waitFor 第二次调用落定
    // （其 resolve 值即 PUBLISHED_V2）后再断言渲染与元素替换。
    await client.invalidateQueries({ queryKey: ['preview-panel'] })
    await waitFor(() => expect(mockFetchPublished.mock.calls.length).toBe(2))
    const secondFrame = screen
      .getByTestId('preview-panel-host')
      .querySelector('iframe')
    expect(secondFrame?.getAttribute('srcdoc')).toContain('published panel v2')
    // 重挂语义：元素替换（而非同元素 srcDoc 导航）。
    expect(secondFrame).not.toBe(firstFrame)
  })
})
