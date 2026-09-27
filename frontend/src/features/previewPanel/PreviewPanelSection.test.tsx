/**
 * PreviewPanelSection 回落路径与草稿显式预览的组件测试（issue #328 / #347 P1 /
 * #528 / #796 返工）：fixture 的 html_hash 一律为内容的 sha256（jsdom 24 无
 * crypto.subtle，用 node:crypto 同步复算，与服务端 bundle_hash 同构）。
 * - 未定制 workspace（published=null）→ 渲染 fallback（现有通用预览）；
 * - 已发布 bundle → bundle host 接管，fallback 不再渲染；
 * - 「定制预览」对话期间草稿**不自动执行**（#347 P1）：左栏继续渲染已发布
 *   版本；显式点「预览此草稿」后才切换到草稿；关闭面板回到已发布版本，
 *   重开面板回到默认态（不记忆执行态）。
 * - #796 返工 wiring：治理动作（预览此草稿/发布草稿/恢复默认）与草稿状态
 *   行在预览区头部（PreviewPanelHeader，真实组件参与渲染）；Dock 收敛为
 *   纯对话（mock 掉）；授权仍锚定 Dock 会话——头部点「预览此草稿」会同时
 *   唤起 Dock。
 * - #528 模式开关的分支在姊妹文件 PreviewPanelSection.mode.test.tsx；
 *   iframe 重挂语义在 PreviewPanelSection.remount.test.tsx。
 *
 * srcdoc 断言一律用「包含」：宿主会在 bundle 头部注入 CSP meta
 * （PreviewPanelHost 的出站网络红线），完整字符串不再等于 bundle 原文。
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
const mockPublish = vi.fn()
const mockArchive = vi.fn()

vi.mock('./previewPanelApi', () => ({
  fetchPublishedPreviewPanel: (...args: unknown[]) =>
    mockFetchPublished(...args),
  fetchPreviewPanelState: (...args: unknown[]) => mockFetchState(...args),
  publishPreviewPanel: (...args: unknown[]) => mockPublish(...args),
  archivePreviewPanel: (...args: unknown[]) => mockArchive(...args),
}))

// 该 jsdom 环境不提供 localStorage：用内存 stub（#528 模式偏好按 workspace
// 持久化；同 useStudioChat.test.tsx 的模式）。
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

// Dock 本体（Studio chat + AgentPanelDock 容器）在 CustomizePreviewDock 自己
// 的测试覆盖；这里只需要「关闭」出口来钉住 Dock 会话与授权的锚定关系。
vi.mock('./CustomizePreviewDock', () => ({
  CustomizePreviewDock: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="customize-dialog">
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
 * 内容，避免「内容变化但 hash 未变」这种后端不可伪造的组合污染重挂/
 * 授权用例（key 指纹吃的就是 html_hash，codex P2 修复）。
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
const DRAFT_V2_HTML = '<!doctype html><html><body>draft v2 panel</body></html>'

// 服务端契约：published v1 与草稿 v1/v2 是互不相同的版本，html_hash 均为
// 内容的 sha256（见 makeBundleSync）。
const PUBLISHED = makeBundleSync(PUBLISHED_HTML, 'published')
const DRAFT = makeBundleSync(DRAFT_HTML, 'draft')
const DRAFT_V2 = makeBundleSync(DRAFT_V2_HTML, 'draft')

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

/**
 * 等治理面草稿数据落进头部状态 Chip 再继续：菜单项 disabled={!draft}，
 * Chip 出现「草稿 v1」时动作才可用（点击早于数据送达是无意义竞态——
 * 授权快照取自组件闭包里的 draft）。
 */
async function waitForDraftInHeader() {
  await screen.findByText(/草稿 v1 · /)
}

/** 「恢复默认」收在 MoreVert 溢出菜单（#796 R4：预览/发布已外露出头部
 * 治理区）：点开菜单再点菜单项。菜单在 fireEvent 的 act 内同步挂载，
 * getByRole 直取即可（兼容 fake timers）。 */
function clickGovernanceAction(name: string | RegExp) {
  fireEvent.click(screen.getByRole('button', { name: '预览治理操作' }))
  fireEvent.click(screen.getByRole('menuitem', { name }))
}

beforeEach(() => {
  mockFetchPublished.mockReset()
  mockFetchState.mockReset()
  mockPublish.mockReset()
  mockArchive.mockReset()
  mockFetchState.mockResolvedValue({
    published: null,
    draft: null,
  } satisfies PreviewPanelState)
  mockPublish.mockResolvedValue(PUBLISHED)
  mockArchive.mockResolvedValue({ published: null, draft: null })
  // #528 模式偏好按 workspace 持久化：用例间不互相泄漏。
  localStorageStub.clear()
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
    mockFetchPublished.mockResolvedValue(PUBLISHED)
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
    mockFetchPublished.mockResolvedValue(PUBLISHED)
    mockFetchState.mockResolvedValue({
      published: PUBLISHED,
      draft: DRAFT,
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
    await waitForDraftInHeader()
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

    // 重新打开面板：回到默认态——一次点击不放行后续会话的草稿执行。
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
      draft: DRAFT,
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
      mockFetchPublished.mockResolvedValue(PUBLISHED)
      // 首轮：草稿 v1 就位。
      mockFetchState.mockResolvedValue({
        published: PUBLISHED,
        draft: DRAFT,
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

      // 发布草稿（面板不关）：draft 变 null，左栏回落已发布版本，
      // 按钮回到「预览此草稿」——授权已失效，不能悬空成「预览草稿中」。
      mockFetchState.mockResolvedValue({
        published: PUBLISHED,
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
      // 授权已失效的真实门控信号：左栏徽标消失（按钮态在 mock 面板里
      // 不可见，srcdoc + 徽标已覆盖门控本身）。

      // 同一 chat 会话里 agent 写入新草稿 v2（「发布后继续改一版」的核心
      // 工作流）：v2 必须重新显式预览，不得继承 v1 的授权自动执行
      // （html_hash 变化即回退未授权，#500 P1-5）。
      mockFetchState.mockResolvedValue({
        published: PUBLISHED,
        draft: DRAFT_V2,
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
        draft: DRAFT,
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

  it('预览中切换 job/workspace 后授权复位：草稿不在新身份的桥上下文里继续执行（review 轮 2 P1）', async () => {
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    vi.useFakeTimers()
    try {
      mockFetchPublished.mockResolvedValue(PUBLISHED)
      mockFetchState.mockResolvedValue({
        published: PUBLISHED,
        draft: DRAFT,
      } satisfies PreviewPanelState)
      const { rerender } = renderSection()
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

      // 同 workspace 切 job（jobs/:jobId 参数变化）：react-router 复用组件
      // 实例，draft 持续非 null、无 null 过渡——授权必须随身份复位，
      // 否则已授权草稿在新 jobId 的桥上下文（getJobDetail/readArtifact
      // 绑 jobId）里继续执行。
      rerender(
        <PreviewPanelSection
          jobId="job-2"
          workspaceId="ws1"
          fallback={<div data-testid="generic-fallback">通用产物预览</div>}
        />
      )
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })
      expect(
        screen
          .getByTestId('preview-panel-host')
          .querySelector('iframe')
          ?.getAttribute('srcdoc')
      ).toContain('published panel')
      expect(screen.queryByText('草稿预览中')).toBeNull()

      // 重新显式预览才在（新 jobId 的）草稿上恢复执行。
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

      // 跨 workspace 导航：目标 workspace 的草稿即便在 react-query 缓存内
      // （draft 全程非 null，无 null 间隙），同样不得无点击自动执行。
      rerender(
        <PreviewPanelSection
          jobId="job-3"
          workspaceId="ws2"
          fallback={<div data-testid="generic-fallback">通用产物预览</div>}
        />
      )
      await act(async () => {
        await vi.runOnlyPendingTimersAsync()
      })
      expect(
        screen
          .getByTestId('preview-panel-host')
          .querySelector('iframe')
          ?.getAttribute('srcdoc')
      ).toContain('published panel')
      expect(screen.queryByText('草稿预览中')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('非 admin 成员不渲染「定制预览」入口与治理行（P4 惯例，治理面端点对其 403）', async () => {
    act(() => {
      useAuthStore.setState({ user: { role: 'member' } as never })
    })
    mockFetchPublished.mockResolvedValue(PUBLISHED)
    renderSection()

    // 面板内容对成员照常渲染，但定制入口、治理溢出菜单与治理面查询都不出现。
    await waitFor(() =>
      expect(screen.getByTestId('preview-panel-host')).toBeInTheDocument()
    )
    expect(screen.queryByRole('button', { name: '定制预览' })).toBeNull()
    expect(screen.queryByRole('button', { name: '预览治理操作' })).toBeNull()
    expect(screen.queryByText(/草稿 v1 · /)).toBeNull()
    expect(mockFetchState).not.toHaveBeenCalled()
    // #528：模式开关是查看偏好而非治理动作，成员同样可见可用（切换行为
    // 的分支覆盖在 PreviewPanelSection.mode.test.tsx）。
    expect(
      screen.getByRole('group', { name: '预览显示模式' })
    ).toBeInTheDocument()
  })

  it('#796 wiring：头部「预览此草稿」逐次授权并唤起 Dock，授权后草稿直接在左栏渲染', async () => {
    // bundle 切换使 host 重挂，jsdom 的 load 事件让宿主 setLoading 脱离
    // act（known noise，同上各 fake-timer 用例的声明方式）。
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    mockFetchPublished.mockResolvedValue(PUBLISHED)
    mockFetchState.mockResolvedValue({
      published: PUBLISHED,
      draft: DRAFT,
    } satisfies PreviewPanelState)
    renderSection()

    // 治理行常驻头部（admin）：状态行 + 未授权时按钮为「预览此草稿」，
    // 左栏继续渲染已发布版本（#347 P1：草稿执行不自动发生）。
    await waitForDraftInHeader()
    expect(screen.queryByTestId('customize-dialog')).toBeNull()
    expect(
      screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
        ?.getAttribute('srcdoc')
    ).toContain('published panel')

    // 显式授权：草稿在左栏渲染（与已发布版本同一 PreviewPanelHost 挂载
    // 点），外露按钮转为禁用的「预览草稿中」（#796 R4），Dock 同步唤起
    // （授权锚定 Dock 会话）。
    fireEvent.click(screen.getByRole('button', { name: '预览此草稿' }))
    await waitFor(() => {
      const iframe = screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
      expect(iframe?.getAttribute('srcdoc')).toContain('draft panel')
    })
    expect(screen.getByText('草稿预览中')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '预览草稿中' })).toBeDisabled()
    expect(screen.getByTestId('customize-dialog')).toBeInTheDocument()
  })

  it('头部治理动作：发布草稿（外露按钮）调用发布 API，恢复默认（⋮ 菜单）需确认后调用归档 API', async () => {
    mockFetchPublished.mockResolvedValue(PUBLISHED)
    mockFetchState.mockResolvedValue({
      published: PUBLISHED,
      draft: DRAFT,
    } satisfies PreviewPanelState)
    renderSection()
    await waitForDraftInHeader()

    // #796 R4：发布草稿外露出治理区（状态 Chip 旁），恢复默认留在 ⋮ 菜单。
    fireEvent.click(screen.getByRole('button', { name: '发布草稿' }))
    await waitFor(() => expect(mockPublish).toHaveBeenCalledWith('ws1'))

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    clickGovernanceAction(/恢复默认（归档）/)
    await waitFor(() => expect(mockArchive).toHaveBeenCalledWith('ws1'))
    confirmSpy.mockRestore()
  })

  it('预览中草稿内容变化（save_draft 覆盖，html_hash 变）回退未授权：新内容需重新显式预览（#500 P1-5）', async () => {
    // 轮询送达走 fake timers（同 codex P2 用例的 known noise 声明）。
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    vi.useFakeTimers()
    try {
      mockFetchPublished.mockResolvedValue(PUBLISHED)
      mockFetchState.mockResolvedValue({
        published: PUBLISHED,
        draft: DRAFT,
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

      // agent 保存了新内容（save_draft 覆盖同一草稿，draft 持续非 null、
      // html_hash 变化、无 null 间隙）：授权不迁移到新内容——回到已发布
      // 版本，堵住「授权后无人值守期间被推送任意新 HTML 自动执行」。
      mockFetchState.mockResolvedValue({
        published: PUBLISHED,
        draft: DRAFT_V2,
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

      // 重新点「预览此草稿」才执行新内容（改一版重新预览一次——工作流
      // 本来的节奏）。
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

  it('授权比对发生在 render 期：jobId 变化的同一 commit 内草稿即回落，无 effect 窗口（#500 P1-3）', async () => {
    // rerender 触发 iframe 重挂（key 变化），jsdom 的 load 事件使宿主
    // setLoading 脱离 act（known noise，同上各用例的声明方式）。
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    // 现有「切换 job/workspace 后授权复位」用例断言的是轮询冲刷后的稳态；
    // 本用例钉住的是更紧的时序——身份/内容变化的首个 commit 就不放行
    // 草稿。用 MutationObserver 同步捕获 iframe srcdoc 的每一次 DOM 提交
    // （jsdom 下 React 逐 commit 同步落 DOM）：若授权复位依赖被动
    // effect，jobId 变化的首帧会先以「新 jobId + 旧授权」渲染——观察者
    // 会捕获到 draft 内容的 srcdoc；render 期派生则首帧即 published。
    mockFetchPublished.mockResolvedValue(PUBLISHED)
    mockFetchState.mockResolvedValue({
      published: PUBLISHED,
      draft: DRAFT,
    } satisfies PreviewPanelState)
    const { rerender } = renderSection()
    await waitFor(() =>
      expect(screen.getByTestId('preview-panel-host')).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('button', { name: '定制预览' }))
    await waitForDraftInHeader()
    fireEvent.click(screen.getByRole('button', { name: '预览此草稿' }))
    await waitFor(() => {
      const iframe = screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
      expect(iframe?.getAttribute('srcdoc')).toContain('draft panel')
    })
    // 冻结后续轮询（首次解析用尽了 mockResolvedValue 的响应）：断言窗口
    // 内只有身份变化这一个变量，3s refetch 不来搅局。
    mockFetchState.mockClear()
    mockFetchState.mockImplementation(
      () => new Promise(() => {}) as Promise<PreviewPanelState>
    )

    // 观察者就位后同 workspace 切 job：记录 section 内 srcdoc 的每一次
    // DOM 变化（含首帧；key 变化会整树重挂 iframe，观察必须落在常驻的
    // section 容器上——宿主 wrapper/iframe 都会被替换，旧引用已 detach）。
    // 首帧必须是 published——「新 jobId 执行旧授权草稿」的窗口为 0。
    const srcdocHistory: string[] = []
    const section = screen.getByTestId('preview-panel-section')
    const readSrcdoc = () =>
      section.querySelector('iframe')?.getAttribute('srcdoc') ?? ''
    const observer = new MutationObserver(() => {
      srcdocHistory.push(readSrcdoc())
    })
    observer.observe(section, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['srcdoc'],
    })
    try {
      rerender(
        <PreviewPanelSection
          jobId="job-2"
          workspaceId="ws1"
          fallback={<div data-testid="generic-fallback">通用产物预览</div>}
        />
      )
      // rerender 同步提交后的立即状态（effect 尚未有机会运行）。
      expect(readSrcdoc()).toContain('published panel')
      expect(screen.queryByText('草稿预览中')).toBeNull()
      // 同步提交期间没有任何一帧是 draft（effect 窗口为 0 的铁证）。
      expect(srcdocHistory).toHaveLength(0)
    } finally {
      observer.disconnect()
    }

    // 重新显式预览才在（新 jobId 的）草稿上恢复执行——同一 commit 生效。
    fireEvent.click(screen.getByRole('button', { name: '预览此草稿' }))
    expect(
      screen
        .getByTestId('preview-panel-host')
        .querySelector('iframe')
        ?.getAttribute('srcdoc')
    ).toContain('draft panel')
    expect(screen.getByText('草稿预览中')).toBeInTheDocument()
  })
})
