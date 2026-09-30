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
  })

  // #799：原顶栏内容拆为双浮岛——身份岛（标题/版本/状态 chip）与操作岛
  // （校验/发布/重置，aria-label 沿用 Workflow command bar）。
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

    expect(await screen.findByText('题目审题 / 编辑工作流')).toBeInTheDocument()
    expect(await screen.findByText('获取题目')).toBeInTheDocument()
  })

  it('renders workspace editor title and actions in the floating islands without workflow label clutter (#799)', async () => {
    renderPage()

    // 无 AppBar：顶栏区不渲染。
    expect(screen.queryByTestId('app-bar')).not.toBeInTheDocument()
    const identity = await screen.findByTestId('studio-identity-island')
    const actions = await screen.findByTestId('studio-action-island')
    await screen.findByText('题目审题 / 编辑工作流')
    expect(identity).toHaveTextContent('题目审题 / 编辑工作流')
    expect(identity).not.toHaveTextContent('知识视频 DAG')
    expect(identity).toHaveTextContent('v1')
    // #799 重组：生命周期动作在左岛（指挥中心），右岛为纯图标组。
    expect(identity).toHaveTextContent('校验')
    expect(identity).toHaveTextContent('发布')
    expect(identity).toHaveTextContent('重置')
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

    expect(await screen.findByText('题目审题 / 编辑工作流')).toBeInTheDocument()
    expect(await screen.findByText('获取题目')).toBeInTheDocument()
    expect(screen.getAllByText(/v1/)[0]).toBeInTheDocument()
    expect(screen.getByText(/abcdef12/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    expect(
      await screen.findByDisplayValue(/key: demo_video_workflow/)
    ).toBeInTheDocument()
  })

  it('shows workflow-wide changes in the changes drawer after validation', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('题目审题 / 编辑工作流')
    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    const editor = await screen.findByLabelText('工作流 YAML')
    await user.type(editor, '\n# edited')
    await closeYamlEditor(user)

    // chip 查询限定身份岛：画布角标（#666 起同源）也带「未发布变更」
    // 文案，整屏 findByText 会多匹配。
    await within(identityIsland()).findByText(/未发布变更/)
    // 校验完成打开右侧变更面板（Drawer），不再切换画布模式。
    await user.click(screen.getByRole('button', { name: '校验' }))
    expect(await screen.findByText('变更与校验')).toBeInTheDocument()
    expect(screen.getByText('变更摘要')).toBeInTheDocument()
  })

  it('opens the changes drawer from the status chip', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('题目审题 / 编辑工作流')
    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    const editor = await screen.findByLabelText('工作流 YAML')
    await user.type(editor, '\n# edited')
    await closeYamlEditor(user)

    // 等 compare 落定、chip 稳定为计数形态再点击：编辑后 chip 先显示瞬态的
    // 「有未发布变更」，compare debounce 一到就被「计算中…」替换——点在被
    // 替换下来的旧节点上点击会静默丢失（慢机器/CI 上必现的竞态）。查询限定
    // 身份岛（画布角标也带「未发布变更」文案，整屏匹配会命中两个）。
    await user.click(
      await within(identityIsland()).findByText(/未发布变更 \d+/)
    )

    expect(await screen.findByText('变更与校验')).toBeInTheDocument()
    expect(screen.getByText('变更摘要')).toBeInTheDocument()
  })

  it('marks the editor dirty and resets to active definition', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('题目审题 / 编辑工作流')
    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    const editor = await screen.findByLabelText('工作流 YAML')
    await user.clear(editor)
    await user.type(editor, 'key: changed')
    await closeYamlEditor(user)

    expect(within(identityIsland()).getByText(/未发布变更/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '重置' }))

    expect(within(identityIsland()).getByText(/已同步/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    expect(
      await screen.findByDisplayValue(/key: demo_video_workflow/)
    ).toBeInTheDocument()
  })

  it('opens publish review dialog before publishing', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('题目审题 / 编辑工作流')
    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    const editor = screen.getByLabelText('工作流 YAML')
    await user.type(editor, '\n# edited')
    await closeYamlEditor(user)

    await within(identityIsland()).findByText(/未发布变更/)
    const publishButton = screen.getByRole('button', { name: '发布新版本' })
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

    await screen.findByText('题目审题 / 编辑工作流')
    await user.click(screen.getByRole('button', { name: '编辑 YAML' }))
    const editor = screen.getByLabelText('工作流 YAML')
    await user.type(editor, '\n# edited')
    await closeYamlEditor(user)

    await within(identityIsland()).findByText(/未发布变更/)
    const publishButton = screen.getByRole('button', { name: '发布新版本' })
    await waitFor(() => expect(publishButton).not.toBeDisabled())
    await user.click(publishButton)
    await screen.findByText('发布 workflow revision')

    await user.click(screen.getByRole('button', { name: '确认发布' }))

    expect(await screen.findByText('保存成功')).toBeInTheDocument()
  })
})
