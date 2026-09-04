import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import { WorkflowNodeAgentEditor } from './WorkflowNodeAgentEditor'

const mocks = {
  fetchAgentDefinition: vi.fn(),
  createAgentDefinition: vi.fn(),
  saveAgentDraft: vi.fn(),
  publishAgent: vi.fn(),
  archiveAgent: vi.fn(),
}

vi.mock('../../../api', () => ({
  fetchAgentDefinition: (...args: unknown[]) =>
    mocks.fetchAgentDefinition(...args),
  createAgentDefinition: (...args: unknown[]) =>
    mocks.createAgentDefinition(...args),
  saveAgentDraft: (...args: unknown[]) => mocks.saveAgentDraft(...args),
  publishAgent: (...args: unknown[]) => mocks.publishAgent(...args),
  archiveAgent: (...args: unknown[]) => mocks.archiveAgent(...args),
}))

function renderEditor(
  props: Partial<React.ComponentProps<typeof WorkflowNodeAgentEditor>> &
    Pick<
      React.ComponentProps<typeof WorkflowNodeAgentEditor>,
      'agentId' | 'capability'
    >
) {
  return render(
    <TestQueryProvider>
      <WorkflowNodeAgentEditor bindingStatus="ready" {...props} />
    </TestQueryProvider>
  )
}

describe('WorkflowNodeAgentEditor', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    useSettingStore.setState({ workspaceId: 'ws1' })
    const { useUiStore } = await import('../../../stores/uiStore')
    useUiStore.setState({ toast: null })
    mocks.fetchAgentDefinition.mockResolvedValue({
      latest: null,
      published: {
        status: 'published',
        definition: {
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: 'demo/skill',
          tools: ['read'],
        },
      },
    })
  })

  // #409：开合按钮已移除——编辑面板在 Agent 区块里直接内联展开。
  it('renders the embedded editor inline without a toggle button and loads the bound agent', async () => {
    renderEditor({ agentId: 'agent-a', capability: 'generate_key_info' })

    expect(
      screen.queryByRole('button', { name: '编辑 Agent' })
    ).not.toBeInTheDocument()
    expect(mocks.fetchAgentDefinition).toHaveBeenCalledWith('ws1', 'agent-a')
    expect(await screen.findByDisplayValue('generate_key_info'))
    expect(screen.getByDisplayValue('agent-a')).toBeInTheDocument()
  })

  it('keeps the loaded skill when editing an existing agent (#76: legacy fallback)', async () => {
    mocks.saveAgentDraft.mockResolvedValue({})
    renderEditor({ agentId: 'agent-a', capability: 'generate_key_info' })

    await screen.findByDisplayValue('generate_key_info')
    // 定义加载自带 skill 的存量 Agent：编辑器不展示 skill，但保存时原样保留
    // （节点未绑 skill 的 workflow 仍靠 AgentDefinition.skill 兜底）。
    expect(screen.queryByDisplayValue('demo/skill')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    // waitFor 的轮询包在 act 里：保存 resolve 后的 busy/toast 状态更新被覆盖。
    await waitFor(() =>
      expect(mocks.saveAgentDraft).toHaveBeenCalledWith(
        'ws1',
        'agent-a',
        expect.objectContaining({
          capability: 'generate_key_info',
          skill: 'demo/skill',
        })
      )
    )
  })

  it('creates a new agent with an empty skill (node-level binding)', async () => {
    mocks.createAgentDefinition.mockResolvedValue({ agent_id: 'agent-new' })
    renderEditor({ agentId: null, capability: 'generate_key_info' })

    fireEvent.change(await screen.findByLabelText('Agent ID'), {
      target: { value: 'agent-new' },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '创建草稿' }))
    })

    // #421 独立复审回归：新建默认 runtime 是 velites（AgentEditor useState
    // 默认值），无意改回 'pi' 时这里必须变红。
    expect(mocks.createAgentDefinition).toHaveBeenCalledWith(
      'ws1',
      expect.objectContaining({
        agent_id: 'agent-new',
        skill: '',
        runtime: 'velites',
      })
    )
  })

  // #387：普通新建（非 switchToAgent）创建的是 draft-only Agent，目录里
  // 查不到；关面板会让「发布」按钮永远不可达——创建后面板必须留在编辑/
  // 发布模式。
  it('stays in publish mode after a plain create (not just switchToAgent)', async () => {
    mocks.createAgentDefinition.mockResolvedValue({ agent_id: 'agent-new' })
    mocks.fetchAgentDefinition.mockResolvedValue({
      latest: {
        status: 'draft',
        definition: {
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: 'demo/skill',
          tools: ['read'],
        },
      },
      published: null,
    })
    renderEditor({ agentId: null, capability: 'generate_key_info' })

    fireEvent.change(await screen.findByLabelText('Agent ID'), {
      target: { value: 'agent-new' },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '创建草稿' }))
    })

    const { useUiStore } = await import('../../../stores/uiStore')
    await vi.waitFor(() =>
      // AgentEditor 的创建 toast（Panel 不再叠加第二条，subagent P3）。
      expect(useUiStore.getState().toast?.message).toBe(
        'Agent「agent-new」草稿已创建'
      )
    )
    // 面板切到编辑/发布模式加载新草稿，「发布」按钮可达。
    await vi.waitFor(() =>
      expect(mocks.fetchAgentDefinition).toHaveBeenCalledWith(
        'ws1',
        'agent-new'
      )
    )
    expect(await screen.findByRole('button', { name: '发布' })).toBeEnabled()
  })

  it('shows the create form prefilled with the node capability', async () => {
    renderEditor({ agentId: null, capability: 'generate_key_info' })

    expect(await screen.findByDisplayValue('generate_key_info'))
    expect(mocks.fetchAgentDefinition).not.toHaveBeenCalled()
  })

  it('stays in publish mode after creating a draft agent (agent node entry, #392)', async () => {
    mocks.createAgentDefinition.mockResolvedValue({ agent_id: 'agent-new' })
    mocks.fetchAgentDefinition.mockResolvedValue({
      latest: {
        status: 'draft',
        definition: {
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: 'demo/skill',
          tools: ['read'],
        },
      },
      published: null,
    })
    renderEditor({ agentId: null, capability: 'generate_key_info' })

    fireEvent.change(await screen.findByLabelText('Agent ID'), {
      target: { value: 'agent-new' },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '创建草稿' }))
    })

    expect(mocks.createAgentDefinition).toHaveBeenCalled()
    // 面板切到编辑/发布模式加载新草稿（/api/agent-catalog 只回
    // published，面板关闭会让用户无法从该入口发布它）。
    await vi.waitFor(() =>
      expect(mocks.fetchAgentDefinition).toHaveBeenCalledWith(
        'ws1',
        'agent-new'
      )
    )
    expect(await screen.findByRole('button', { name: '发布' })).toBeEnabled()
  })

  it('renders nothing in read-only mode or without a workspace', () => {
    const { container, unmount } = renderEditor({
      agentId: 'agent-a',
      capability: 'cap',
      readOnly: true,
    })
    expect(container).toBeEmptyDOMElement()
    // 先卸载再改全局 store，避免已挂载订阅者在 act 外更新。
    unmount()

    useSettingStore.setState({ workspaceId: null })
    const { container: noWorkspace } = renderEditor({
      agentId: 'agent-a',
      capability: 'cap',
    })
    expect(noWorkspace).toBeEmptyDOMElement()
  })

  // #426 review P1：未绑定节点上创建草稿后切到另一个未绑定节点（agentId
  // 仍为 null）——面板必须以新 capability 全新挂载（key={capability}），不
  // 带着 A 的 createdAgentId 继续编辑 A 的草稿；#409 去掉开合按钮后已无
  // 「收起重置」入口。同 capability 切换不重挂（编辑目标不变，表单不丢）。
  it('resets the draft state when switching to a different unbound capability', async () => {
    mocks.createAgentDefinition.mockResolvedValue({ agent_id: 'agent-new' })
    mocks.fetchAgentDefinition.mockResolvedValue({
      latest: {
        status: 'draft',
        definition: {
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: 'demo/skill',
          tools: ['read'],
        },
      },
      published: null,
    })
    const view = (capability: string) => (
      <TestQueryProvider>
        <WorkflowNodeAgentEditor
          agentId={null}
          capability={capability}
          bindingStatus="ready"
        />
      </TestQueryProvider>
    )
    const { rerender } = render(view('generate_key_info'))

    // 节点 A：填 ID 创建草稿，面板留在 A 草稿的编辑/发布模式。
    fireEvent.change(await screen.findByLabelText('Agent ID'), {
      target: { value: 'agent-new' },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '创建草稿' }))
    })
    await screen.findByRole('button', { name: '发布' })

    // 切到节点 B（capability 变化，agentId 仍 null）：面板重挂成 B 的新建
    // 表单——A 的创建按钮/发布模式不再出现，表单 capability 是 B 的。
    rerender(view('review'))

    expect(
      screen.queryByRole('button', { name: '发布' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '版本历史' })
    ).not.toBeInTheDocument()
    expect(await screen.findByLabelText('Agent ID')).toHaveValue('')
    expect(screen.getByDisplayValue('review')).toBeInTheDocument()
  })

  // #426 独立复审 P2：面板内创建的草稿归档后（#409 无收合重置入口），
  // createdAgentId 必须清空——回落新建表单，不残留已归档 Agent 的编辑态。
  it('returns to the create form after archiving the draft created in the panel', async () => {
    mocks.createAgentDefinition.mockResolvedValue({ agent_id: 'agent-new' })
    mocks.fetchAgentDefinition.mockResolvedValue({
      latest: {
        status: 'draft',
        definition: {
          capability: 'generate_key_info',
          runtime: 'pi',
          skill: 'demo/skill',
          tools: ['read'],
        },
      },
      published: null,
    })
    mocks.archiveAgent.mockResolvedValue({})
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderEditor({ agentId: null, capability: 'generate_key_info' })

    // 面板内创建草稿 → 留在编辑/发布模式（绑定 agent-new）。
    fireEvent.change(await screen.findByLabelText('Agent ID'), {
      target: { value: 'agent-new' },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '创建草稿' }))
    })
    await screen.findByRole('button', { name: '发布' })

    // 归档该草稿：面板回落新建表单（key 重挂成 '__new__'），不残留
    // 已归档 Agent 的可编辑态。
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '归档' }))
    })

    expect(confirmSpy).toHaveBeenCalledWith('确定要归档 Agent「agent-new」吗？')
    expect(mocks.archiveAgent).toHaveBeenCalledWith('ws1', 'agent-new')
    await screen.findByLabelText('Agent ID')
    expect(
      screen.queryByRole('button', { name: '发布' })
    ).not.toBeInTheDocument()
    expect(screen.getByLabelText('Agent ID')).toHaveValue('')
    confirmSpy.mockRestore()
  })

  // #426 review P2：目录/定义查询未 settle 时 agentId=null 是「未知」而非
  // 「未绑定」——只渲染加载占位，不出可操作的新建表单；settle 后按绑定
  // 结果正常出表单。
  it('shows a loading placeholder instead of the create form until the binding query settles', async () => {
    const view = (bindingStatus: 'pending' | 'ready') => (
      <TestQueryProvider>
        <WorkflowNodeAgentEditor
          agentId={null}
          capability="generate_key_info"
          bindingStatus={bindingStatus}
        />
      </TestQueryProvider>
    )
    const { rerender } = render(view('pending'))

    expect(screen.getByText('Agent 绑定解析中...')).toBeInTheDocument()
    expect(screen.queryByLabelText('Agent ID')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '创建草稿' })
    ).not.toBeInTheDocument()

    rerender(view('ready'))

    expect(screen.queryByText('Agent 绑定解析中...')).not.toBeInTheDocument()
    expect(await screen.findByLabelText('Agent ID')).toBeInTheDocument()
    expect(screen.getByDisplayValue('generate_key_info')).toBeInTheDocument()
  })

  // #426 review P2：查询失败 ≠ 确认未绑定——错误提示而非可操作表单（顶部
  // 有全局重试横幅），否则失败场景等于回到 P2。#426 终局复审 P1 起首次
  // 失败（从未 ready）仍渲染纯占位。
  it('shows an error placeholder instead of the create form when the binding query failed', () => {
    renderEditor({ agentId: null, capability: 'cap', bindingStatus: 'error' })

    expect(screen.getByText('Agent 目录加载失败')).toBeInTheDocument()
    // 失败提示用 alert 语义（对齐 AgentEditor 错误条先例）。
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.queryByLabelText('Agent ID')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '创建草稿' })
    ).not.toBeInTheDocument()
  })

  // #426 终局复审 P1（turn_end 失效路径）：ready 后 invalidate 触发的
  // 后台重取把 bindingStatus 翻回 pending——编辑器不再卸载（表单是本地
  // useState，卸载即丢未保存输入），改为保挂载 + 冻结提示条；重取完成
  // （ready 恢复）后提示消失、输入保留、可继续编辑。
  it('keeps the mounted editor with inputs intact when a ready binding flips back to pending', async () => {
    const view = (bindingStatus: 'pending' | 'ready') => (
      <TestQueryProvider>
        <WorkflowNodeAgentEditor
          agentId="agent-a"
          capability="generate_key_info"
          bindingStatus={bindingStatus}
        />
      </TestQueryProvider>
    )
    const { rerender } = render(view('ready'))

    // 编辑器已挂载并加载详情。
    expect(
      await screen.findByDisplayValue('generate_key_info')
    ).toBeInTheDocument()
    // 模拟用户在 config_schema 输入未保存内容。
    const schemaField = await screen.findByLabelText(
      'config_schema（JSON，可空）'
    )
    fireEvent.change(schemaField, { target: { value: '{"x":1}' } })
    expect(schemaField).toHaveValue('{"x":1}')

    // turn_end 失效 → 后台重取在途 → bindingStatus 翻回 pending。
    act(() => {
      rerender(view('pending'))
    })

    expect(
      screen.getByText('Agent 目录刷新中，编辑暂缓...')
    ).toBeInTheDocument()
    // 表单仍挂载：输入值未丢，也没有重复拉详情（AgentEditor 未重挂）。
    // 终局收尾 P3-1：冻结容器带 inert（键盘焦点/读屏与指针一起阻断，
    // CSS pointer-events 只挡指针）——编辑器在 inert 子树内且值未丢。
    const frozenField = screen.getByLabelText('config_schema（JSON，可空）')
    expect(frozenField).toHaveValue('{"x":1}')
    expect(frozenField.closest('[inert]')).not.toBeNull()
    expect(mocks.fetchAgentDefinition).toHaveBeenCalledTimes(1)

    // 重取完成（ready 恢复）：提示消失，输入保留，可继续编辑。
    act(() => {
      rerender(view('ready'))
    })
    expect(
      screen.queryByText('Agent 目录刷新中，编辑暂缓...')
    ).not.toBeInTheDocument()
    // inert 随冻结移除，键盘路径恢复可达。
    expect(
      screen.getByLabelText('config_schema（JSON，可空）').closest('[inert]')
    ).toBeNull()
    fireEvent.change(screen.getByLabelText('config_schema（JSON，可空）'), {
      target: { value: '{"x":2}' },
    })
    expect(screen.getByLabelText('config_schema（JSON，可空）')).toHaveValue(
      '{"x":2}'
    )
    expect(mocks.fetchAgentDefinition).toHaveBeenCalledTimes(1)
  })

  // #426 终局复审 P1（error 侧）：曾 ready 后重取失败（bindingStatus=
  // error）——输入保留 + 错误提示 + 不放可操作编辑（重试走全局横幅）；
  // 恢复 ready 后输入仍在。
  it('keeps the inputs and shows the error notice when a refetch fails after ready', async () => {
    const view = (bindingStatus: 'ready' | 'error') => (
      <TestQueryProvider>
        <WorkflowNodeAgentEditor
          agentId="agent-a"
          capability="generate_key_info"
          bindingStatus={bindingStatus}
        />
      </TestQueryProvider>
    )
    const { rerender } = render(view('ready'))

    const schemaField = await screen.findByLabelText(
      'config_schema（JSON，可空）'
    )
    fireEvent.change(schemaField, { target: { value: '{"x":1}' } })

    act(() => {
      rerender(view('error'))
    })

    expect(
      screen.getByText('Agent 目录刷新失败，重试前编辑暂缓。')
    ).toBeInTheDocument()
    expect(screen.getByRole('alert')).toBeInTheDocument()
    // 终局收尾 P3-1：error 侧冻结同样带 inert（提示条在 inert 区外仍可读）。
    const frozenField = screen.getByLabelText('config_schema（JSON，可空）')
    expect(frozenField).toHaveValue('{"x":1}')
    expect(frozenField.closest('[inert]')).not.toBeNull()
    expect(mocks.fetchAgentDefinition).toHaveBeenCalledTimes(1)

    act(() => {
      rerender(view('ready'))
    })
    expect(screen.getByLabelText('config_schema（JSON，可空）')).toHaveValue(
      '{"x":1}'
    )
  })

  // #426 终局复审 P3-2：编辑器自身保存草稿触发 refresh（invalidate 双查
  // 询）→ bindingStatus 短暂翻回 pending——面板不重挂（无卸载闪烁、不
  // 重复拉详情），已编辑的表单值保留。
  it('does not remount the editor after saving a draft triggers the catalog refresh', async () => {
    mocks.saveAgentDraft.mockResolvedValue({})
    const view = (bindingStatus: 'ready' | 'pending') => (
      <TestQueryProvider>
        <WorkflowNodeAgentEditor
          agentId="agent-a"
          capability="generate_key_info"
          bindingStatus={bindingStatus}
        />
      </TestQueryProvider>
    )
    const { rerender } = render(view('ready'))

    const schemaField = await screen.findByLabelText(
      'config_schema（JSON，可空）'
    )
    fireEvent.change(schemaField, { target: { value: '{"x":1}' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))
    })
    expect(mocks.saveAgentDraft).toHaveBeenCalledWith(
      'ws1',
      'agent-a',
      expect.objectContaining({ config_schema: { x: 1 } })
    )

    // 保存后的 invalidate 窗口：pending 期面板保挂载，表单值不丢。
    act(() => {
      rerender(view('pending'))
    })
    expect(
      screen.getByText('Agent 目录刷新中，编辑暂缓...')
    ).toBeInTheDocument()
    expect(screen.getByLabelText('config_schema（JSON，可空）')).toHaveValue(
      '{"x":1}'
    )
    expect(mocks.fetchAgentDefinition).toHaveBeenCalledTimes(1)
  })

  // #426 codex P2 修正：agentId 非空不再无条件放行——目录未 settle 时它
  // 可能只是 draft 回落先行（definitions 先返回同 capability 草稿，catalog
  // 仍在途）。若立即放行，settle 后同 capability 的 published Agent 会把
  // 编辑目标切走并重挂 AgentEditor，丢掉用户已输入的内容、提前发布还会撞
  // 冲突。占位直到目录 ready（「published ?? draft」终态）才放行。
  it('keeps the placeholder for a draft-fallback agentId until the catalog settles, then renders the editor', async () => {
    const view = (bindingStatus: 'pending' | 'error' | 'ready') => (
      <TestQueryProvider>
        <WorkflowNodeAgentEditor
          agentId="draft-agent"
          capability="generate_key_info"
          bindingStatus={bindingStatus}
        />
      </TestQueryProvider>
    )
    const { rerender } = render(view('pending'))

    // 目录在途：draft 回落的 agentId 也只渲染占位，不挂编辑器。
    expect(screen.getByText('Agent 绑定解析中...')).toBeInTheDocument()
    expect(mocks.fetchAgentDefinition).not.toHaveBeenCalled()

    // 目录失败且无数据：错误占位（同样不放行）。
    rerender(view('error'))
    expect(screen.getByText('Agent 目录加载失败')).toBeInTheDocument()
    expect(mocks.fetchAgentDefinition).not.toHaveBeenCalled()

    // 目录 ready：绑定已是终态（published 命中或确认无 published 的
    // draft 回落），放行渲染编辑器。
    rerender(view('ready'))
    expect(await screen.findByDisplayValue('draft-agent')).toBeInTheDocument()
    expect(mocks.fetchAgentDefinition).toHaveBeenCalledWith(
      'ws1',
      'draft-agent'
    )
  })

  // #426 codex P2（上一轮语义的 published 命中侧）：目录已 settle（ready）
  // 且 published 命中 agentId 时，另一份查询（agent-definitions）在途/失败
  // 不再影响门控——AgentEditor 按 ID 加载详情不依赖列表，错误由全局横幅
  // 暴露（bindingStatus 现只按目录 settle 计算，本用例即回归该放行语义）。
  it('renders the editor for a published-catalog agentId once the catalog has settled', async () => {
    renderEditor({ agentId: 'agent-a', capability: 'generate_key_info' })

    expect(screen.queryByText('Agent 绑定解析中...')).not.toBeInTheDocument()
    expect(mocks.fetchAgentDefinition).toHaveBeenCalledWith('ws1', 'agent-a')
    expect(await screen.findByDisplayValue('agent-a')).toBeInTheDocument()
    expect(screen.getByDisplayValue('generate_key_info')).toBeInTheDocument()
  })
})
