import React from 'react'
import {
  render,
  screen,
  waitFor,
  waitForElementToBeRemoved,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkflowStudioPage } from './WorkflowStudioPage'
import { TestQueryProvider } from '../testing/testQueryClient'
import { useUiStore } from '../stores/uiStore'

vi.mock('react-router-dom', () => ({
  useParams: () => ({ workspaceId: 'ws1' }),
  useNavigate: () => vi.fn(),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  Navigate: ({ to }: { to: string }) => (
    <div data-testid="route-navigate" data-to={to} />
  ),
}))

const authState: { user: { role: 'admin' | 'member' } | null } = {
  user: { role: 'admin' },
}
vi.mock('../stores/authStore', () => ({
  useAuthStore: (selector?: (state: typeof authState) => unknown) =>
    selector ? selector(authState) : authState,
}))

vi.mock('../features/workflowStudio/chat/StudioChatPanel', () => ({
  StudioChatPanel: () => <div>chat panel stub</div>,
}))

function renderPage() {
  return render(
    <TestQueryProvider>
      <WorkflowStudioPage />
    </TestQueryProvider>
  )
}

vi.mock('../api', () => {
  const activeRevisionPayload = {
    revision: {
      id: 'ws1:demo_video_workflow:v1',
      workspace_id: 'ws1',
      workflow_key: 'demo_video_workflow',
      version: 1,
      status: 'active',
      definition_hash: 'abcdef1234567890',
      created_at: '2026-07-02T00:00:00Z',
      published_at: '2026-07-02T00:00:00Z',
    },
    workflow: {
      key: 'demo_video_workflow',
      label: '知识视频 DAG',
      intake: { modes: [] },
      edges: [
        {
          source: 'fetch_items',
          target: 'clean_items',
          condition: null,
        },
      ],
      nodes: [
        {
          key: 'fetch_items',
          label: '获取题目',
          capability: 'fetch_items',
          after: [],
          inputs: [],
          outputs: ['questions.json'],
        },
        {
          key: 'clean_items',
          label: '清洗与解析',
          capability: 'clean_items',
          after: ['fetch_items'],
          inputs: ['questions.json'],
          outputs: ['questions_parsed.json'],
        },
      ],
    },
    // definition_yaml 与真实后端一致：active revision 的完整定义序列化
    // （含 nodes/edges——revision_format 恒序列化全量定义）。此前 mock 只有
    // key/label 两行，draft 记录解析出 0 节点并覆盖 activeWorkflow 派生的
    // DAG，快机器上基线同步先于 findByText 完成导致画布恒空（环境竞态）。
    definition_yaml: [
      'key: demo_video_workflow',
      'label: 知识视频 DAG',
      'nodes:',
      '  fetch_items:',
      '    label: 获取题目',
      '    capability: fetch_items',
      '    outputs: [questions.json]',
      '  clean_items:',
      '    label: 清洗与解析',
      '    capability: clean_items',
      '    after: [fetch_items]',
      '    inputs: [questions.json]',
      '    outputs: [questions_parsed.json]',
      'edges:',
      '  - from: fetch_items',
      '    to: clean_items',
      '',
    ].join('\n'),
  }

  return {
    api: vi.fn((path: string) => {
      if (path === '/api/workspaces/ws1') {
        return Promise.resolve({
          workspace: { id: 'ws1', name: '题目审题' },
        })
      }
      return Promise.reject(new Error(`Unhandled API path: ${path}`))
    }),
    // 列表里不含 ws1，useWorkspaceDisplayName 走单 workspace 回退加载。
    fetchWorkspaces: vi.fn().mockResolvedValue({ workspaces: [] }),
    fetchActiveWorkflowRevision: vi
      .fn()
      .mockResolvedValue(activeRevisionPayload),
    fetchWorkflowRevisions: vi.fn().mockResolvedValue({
      revisions: [activeRevisionPayload.revision],
    }),
    publishWorkflowDraft: vi
      .fn()
      .mockResolvedValue({ valid: true, errors: [] }),
    fetchWorkflowDraft: vi
      .fn()
      .mockResolvedValue({ definition_yaml: null, updated_at: null }),
    putWorkflowDraft: vi.fn().mockResolvedValue({
      definition_yaml: 'key: demo_video_workflow\n',
      updated_at: '2026-08-27T00:00:00+00:00',
    }),
    validateWorkflowDraft: vi
      .fn()
      .mockResolvedValue({ valid: true, errors: [] }),
    compareWorkflowDraft: vi.fn().mockResolvedValue({
      valid: true,
      base_revision: {
        id: activeRevisionPayload.revision.id,
        workflow_key: activeRevisionPayload.revision.workflow_key,
        version: activeRevisionPayload.revision.version,
        definition_hash: activeRevisionPayload.revision.definition_hash,
      },
      draft_workflow: {
        key: activeRevisionPayload.workflow.key,
        label: activeRevisionPayload.workflow.label,
        version: activeRevisionPayload.revision.version + 1,
      },
      summary: {
        risk_level: 'info',
        node_changes: [
          {
            type: 'added',
            node_key: 'new_node',
            label: '新节点',
            fields: [],
            risk: 'info',
          },
        ],
        edge_changes: [],
        intake_changes: [],
        risk_flags: [],
      },
      errors: [],
    }),
  }
})

describe('WorkflowStudioPage', () => {
  beforeEach(() => {
    authState.user = { role: 'admin' }
    useUiStore.setState({ toast: null })
  })

  // #799：原顶栏内容拆为双浮岛——身份岛（workspace 名/版本/状态 chip +
  // 生命周期动作），操作岛（Agent 面板开关/共享素材）。
  const identityIsland = () => screen.getByTestId('studio-identity-island')

  // 关闭 YAML 全屏 Dialog 并等退出过渡结束：过渡期间 modal 仍挂着，
  // 顶栏被 aria-hidden，role 查询会失败。#795 PR②：Agent Dock 也是常驻的
  // role=dialog——断言必须按名定位到「编辑 YAML」，不能用泛 dialog 查询。
  async function closeYamlEditor(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', { name: 'close YAML editor' }))
    await waitForElementToBeRemoved(() =>
      screen.queryByRole('dialog', { name: '编辑 YAML' })
    )
  }

  it('redirects non-admin users away from the studio (P4)', () => {
    authState.user = { role: 'member' }
    renderPage()

    const redirect = screen.getByTestId('route-navigate')
    expect(redirect).toHaveAttribute('data-to', '/workspaces/ws1')
    expect(screen.queryByTestId('app-bar')).not.toBeInTheDocument()
  })

  it('renders the workflow studio shell', async () => {
    renderPage()

    expect(await screen.findByText('题目审题')).toBeInTheDocument()
    expect(await screen.findByText('获取题目')).toBeInTheDocument()
  })

  it('renders workspace editor title and actions in the floating islands without workflow label clutter (#799/#804)', async () => {
    renderPage()

    // 无 AppBar：顶栏区不渲染。
    expect(screen.queryByTestId('app-bar')).not.toBeInTheDocument()
    const identity = await screen.findByTestId('studio-identity-island')
    const actions = await screen.findByTestId('studio-action-island')
    await screen.findByText('题目审题')
    // #804 定案：标题只剩 workspace 名。
    expect(identity).toHaveTextContent('题目审题')
    expect(identity).not.toHaveTextContent('/ 编辑工作流')
    expect(identity).not.toHaveTextContent('知识视频 DAG')
    expect(identity).toHaveTextContent('v1')
    // #804 定案：生命周期动作在左岛——发布 = contained 主按钮（文案「发布」）；
    // #770 起重置收进版本菜单；校验按钮（自动校验取代）与 ⋮ 菜单退役；
    // 干净态无状态 chip。
    expect(
      within(identity).getByRole('button', { name: '发布' })
    ).toBeInTheDocument()
    expect(within(identity).queryByRole('button', { name: '校验' })).toBeNull()
    expect(
      within(identity).queryByRole('button', { name: '更多操作' })
    ).toBeNull()
    expect(within(identity).queryByRole('button', { name: '重置' })).toBeNull()
    expect(within(identity).queryByText('已同步')).toBeNull()
    expect(actions).not.toHaveTextContent('校验')
    // 用量入口从 studio 拿掉（实例级遥测，其他页面全局顶栏已有）。
    expect(
      screen.queryByRole('button', { name: 'Token 使用分析' })
    ).not.toBeInTheDocument()
    // P3：查看变更 / YAML 高级编辑 / Agent 管理 / Executor 管理已从顶栏移除，
    // 前两者下沉为变更 Drawer 与 YAML 全屏 Dialog，后两者随管理弹窗删除。
    expect(identity).not.toHaveTextContent('查看变更')
    expect(identity).not.toHaveTextContent('YAML 高级编辑')
    expect(identity).not.toHaveTextContent('Agent 管理')
    expect(identity).not.toHaveTextContent('Executor 管理')
    expect(
      screen.queryByRole('region', { name: 'Workflow summary' })
    ).not.toBeInTheDocument()
  })

  it('renders active revision metadata and prefilled definition', async () => {
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText('题目审题')).toBeInTheDocument()
    expect(await screen.findByText('获取题目')).toBeInTheDocument()
    expect(screen.getAllByText(/v1/)[0]).toBeInTheDocument()
    // #770：hash 降级为版本触发键的 tooltip / aria-label，不占岛面。
    expect(
      screen.getByRole('button', { name: /版本 v1 · abcdef12/ })
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    expect(
      await screen.findByDisplayValue(/key: demo_video_workflow/)
    ).toBeInTheDocument()
  })

  it('自动校验驱动状态 chip：保存成功后 未发布变更 → ✓ 校验通过，点击开变更抽屉（#804 定案）', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('题目审题')
    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    const editor = await screen.findByLabelText('工作流 YAML')
    await user.type(editor, '\n# edited')
    await closeYamlEditor(user)

    // chip 查询限定身份岛：画布角标（#666 起同源）也带「未发布变更」
    // 文案，整屏 findByText 会多匹配。
    await within(identityIsland()).findByText(/未发布变更/)
    // #804 定案：手动校验按钮退役——自动保存（800ms debounce）落盘后自动
    // 静默校验，chip 转「✓ 校验通过」（不自动开抽屉）。
    const passedChip = await within(identityIsland()).findByText(
      '✓ 校验通过',
      undefined,
      { timeout: 4000 }
    )
    expect(
      screen.queryByRole('dialog', { name: '变更与校验' })
    ).not.toBeInTheDocument()
    // 点 chip 才开校验报告抽屉。
    await user.click(passedChip)
    expect(await screen.findByText('变更与校验')).toBeInTheDocument()
    expect(screen.getByText('变更摘要')).toBeInTheDocument()
  })

  it('自动校验失败：chip 变红 ✗ 校验失败且发布禁用（#804 定案）', async () => {
    const { validateWorkflowDraft } = await import('../api')
    // 只让本例的下一次校验失败（once 队列，不污染后续用例的默认成功 mock）。
    vi.mocked(validateWorkflowDraft).mockResolvedValueOnce({
      valid: false,
      errors: ['missing key'],
    })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('题目审题')
    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    const editor = await screen.findByLabelText('工作流 YAML')
    await user.type(editor, '\n# edited')
    await closeYamlEditor(user)

    await within(identityIsland()).findByText(/未发布变更/)
    await within(identityIsland()).findByText('✗ 校验失败', undefined, {
      timeout: 4000,
    })
    // 发布按钮由自动校验结果门控：失败即禁用。
    const publish = within(identityIsland()).getByRole('button', {
      name: '发布',
    })
    expect(publish).toBeDisabled()
    expect(publish.parentElement).toHaveAttribute(
      'aria-label',
      '校验失败，请修复后重新发布'
    )
  })

  it('marks the editor dirty and resets to active definition', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('题目审题')
    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    const editor = await screen.findByLabelText('工作流 YAML')
    await user.clear(editor)
    await user.type(editor, 'key: changed')
    await closeYamlEditor(user)

    expect(within(identityIsland()).getByText(/未发布变更/)).toBeInTheDocument()

    // #770 顶栏减法：重置不再外露按钮，收进版本菜单；轮 6 H5 的
    // window.confirm 确认保留（jsdom 未实现 confirm，桩成通过）。
    expect(
      within(identityIsland()).queryByRole('button', { name: '重置' })
    ).toBeNull()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    await user.click(
      within(identityIsland()).getByRole('button', { name: /版本 v1/ })
    )
    await user.click(screen.getByRole('menuitem', { name: '重置为已发布版本' }))
    expect(confirmSpy).toHaveBeenCalledOnce()
    confirmSpy.mockRestore()

    // 干净态：状态 chip 消失。
    await waitFor(() =>
      expect(
        within(identityIsland()).queryByText(/未发布变更/)
      ).not.toBeInTheDocument()
    )
    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    expect(
      await screen.findByDisplayValue(/key: demo_video_workflow/)
    ).toBeInTheDocument()
  })

  it('opens publish review dialog before publishing', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('题目审题')
    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    const editor = screen.getByLabelText('工作流 YAML')
    await user.type(editor, '\n# edited')
    await closeYamlEditor(user)

    await within(identityIsland()).findByText(/未发布变更/)
    const publishButton = within(identityIsland()).getByRole('button', {
      name: '发布',
    })
    await waitFor(() => expect(publishButton).not.toBeDisabled())
    await user.click(publishButton)

    expect(
      await screen.findByText('发布 workflow revision')
    ).toBeInTheDocument()
    expect(screen.getByText('确认发布')).toBeInTheDocument()
  })

  it('publishes after confirming the review dialog', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('题目审题')
    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    const editor = screen.getByLabelText('工作流 YAML')
    await user.type(editor, '\n# edited')
    await closeYamlEditor(user)

    await within(identityIsland()).findByText(/未发布变更/)
    const publishButton = within(identityIsland()).getByRole('button', {
      name: '发布',
    })
    await waitFor(() => expect(publishButton).not.toBeDisabled())
    await user.click(publishButton)
    await screen.findByText('发布 workflow revision')

    // CI 慢机加固：确认按钮等 enabled 再点（dialog 进场过渡期间 userEvent
    // 的点击可能被吞）；对话框退场证明 onConfirm 已起跑；toast 直读
    // uiStore（DOM toast 3s 自动消失，慢机上 DOM 轮询会错过窗口）。
    const confirmButton = await screen.findByRole('button', {
      name: '确认发布',
    })
    await waitFor(() => expect(confirmButton).toBeEnabled())
    await user.click(confirmButton)
    await waitFor(
      () =>
        expect(
          screen.queryByRole('dialog', { name: /发布 workflow revision/ })
        ).toBeNull(),
      { timeout: 4000 }
    )
    const { getState } = useUiStore
    await waitFor(() => expect(getState().toast?.message).toBe('保存成功'), {
      timeout: 4000,
    })
  })
})
