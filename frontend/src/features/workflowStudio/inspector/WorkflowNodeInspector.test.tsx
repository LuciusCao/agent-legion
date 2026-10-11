import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { MemoryRouter as ReactRouterMemoryRouter } from 'react-router-dom'
import yaml from 'js-yaml'
import { WorkflowNodeInspector } from './WorkflowNodeInspector'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { api } from '../../../api'
import { useSettingStore } from '../../../stores/settingStore'
import { createTestQueryClient } from '../../../testing/testQueryClient'

vi.mock('../../../api', () => ({
  fetchAgentRuntimes: vi.fn(() => Promise.resolve({ runtimes: {} })),
  api: vi.fn(),
}))

const mockApi = vi.mocked(api)

function wrapper({ children }: { children: ReactNode }) {
  return createElement(
    QueryClientProvider,
    { client: createTestQueryClient() },
    createElement(
      ReactRouterMemoryRouter,
      // #593：start 契约编辑器渲染外部服务连接的管理员入口 Link。
      { future: { v7_startTransition: true, v7_relativeSplatPath: true } },
      children
    )
  )
}

const draftYaml = [
  'key: demo',
  'nodes:',
  '  _start:',
  '    type: start',
  '  intake:',
  '    label: 读取知识点',
  '    capability: intake',
  '    after: [_start]',
  'edges:',
  '  - from: _start',
  '    to: intake',
  '',
].join('\n')

// 中游审批门（#405）：sanitize 切入 approval 已剥 capability，结构化 UI
// 对 approval 又隐藏能力 Key 输入——切回 code/agent 只能经弹窗补。
const approvalGateYaml = [
  'key: demo',
  'nodes:',
  '  _start:',
  '    type: start',
  '  draft_gen:',
  '    type: code',
  '    label: 起草',
  '    capability: draft_gen',
  '    after: [_start]',
  '  gate:',
  '    type: approval',
  '    label: 审批',
  '    config: {rework_target: draft_gen, feedback_artifact: review.json}',
  '    after: [draft_gen]',
  'edges:',
  '  - from: _start',
  '    to: draft_gen',
  '',
].join('\n')

function renderInspector(
  selectedNodeKey: string | null,
  options?: {
    definitionYaml?: string
    compareSummary?: ChangeSummaryViewModel | null
  }
) {
  return render(
    <WorkflowNodeInspector
      workflow={null}
      selectedNodeKey={selectedNodeKey}
      definitionYaml={options?.definitionYaml ?? draftYaml}
      setDefinitionYaml={() => {}}
      compareSummary={options?.compareSummary}
      onClose={() => {}}
    />,
    { wrapper }
  )
}

describe('WorkflowNodeInspector for draft-only (ghost) nodes', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockApi.mockResolvedValue({
      origin: 'none',
      code: '',
      has_draft: false,
      draft_code: null,
      draft_version: null,
    })
    useSettingStore.setState({ workspaceId: 'ws1' })
  })

  it('renders the editable sections for a ghost executable node', async () => {
    renderInspector('intake')

    // 基本设置（YAML 结构化编辑）可用。
    expect(await screen.findByLabelText('节点名称')).toHaveValue('读取知识点')
    expect(screen.getByLabelText('能力')).toHaveValue('intake')
    // 执行能力 / 数据契约 / 依赖段齐全。
    expect(screen.getByLabelText('节点执行能力')).toBeInTheDocument()
    expect(screen.getByText('数据契约')).toBeInTheDocument()
    expect(screen.getByText('依赖关系')).toBeInTheDocument()
    // 不再是「选择一个节点」空态。
    expect(screen.queryByText('选择一个节点')).not.toBeInTheDocument()
  })

  it('shows the node type selector on the header (#392)', async () => {
    renderInspector('intake')

    // code 节点头部有类型选择器（原生 select），三选一；start 不在选项里。
    const selector = await screen.findByLabelText('节点类型')
    expect(selector).toHaveValue('code')
    expect(screen.getByRole('option', { name: 'Code' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Agent' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '审批门' })).toBeInTheDocument()
    expect(
      screen.queryByRole('option', { name: 'start' })
    ).not.toBeInTheDocument()
  })

  it('switches the node type via the selector and sanitizes fields', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const setDefinitionYaml = vi.fn()
    // intake 有可执行上游 draft_gen（approval 入边前置校验要求）。
    const yamlWithAgentFields = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '  draft_gen:',
      '    type: code',
      '    label: 起草',
      '    capability: draft_gen',
      '    after: [_start]',
      '  intake:',
      '    type: agent',
      '    label: 读取知识点',
      '    capability: intake',
      '    skill: demo/skill',
      '    after: [draft_gen]',
      'edges:',
      '  - from: _start',
      '    to: draft_gen',
      '',
    ].join('\n')
    render(
      <WorkflowNodeInspector
        workflow={null}
        selectedNodeKey="intake"
        definitionYaml={yamlWithAgentFields}
        setDefinitionYaml={setDefinitionYaml}
        onClose={() => {}}
      />,
      { wrapper }
    )

    await screen.findByLabelText('节点执行能力')
    fireEvent.change(await screen.findByLabelText('节点类型'), {
      target: { value: 'approval' },
    })

    // 切 approval 是破坏性清洗：先确认（取消路径见下个用例）。
    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringContaining('切换为审批门将清除')
    )
    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    // 节点级断言（js-yaml 解析，避免 toContain 被其他节点的 type 行命中）。
    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    const node = (
      yaml.load(nextYaml) as {
        nodes?: Record<string, Record<string, unknown>>
      }
    ).nodes?.intake
    expect(node?.type).toBe('approval')
    // capability/skill 按目标类型清洗剥除。
    expect(node).not.toHaveProperty('capability')
    expect(node).not.toHaveProperty('skill')
  })

  it('keeps the draft untouched when the approval-switch confirm is dismissed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const setDefinitionYaml = vi.fn()
    render(
      <WorkflowNodeInspector
        workflow={null}
        selectedNodeKey="intake"
        definitionYaml={draftYaml}
        setDefinitionYaml={setDefinitionYaml}
        onClose={() => {}}
      />,
      { wrapper }
    )

    await screen.findByLabelText('节点执行能力')
    fireEvent.change(await screen.findByLabelText('节点类型'), {
      target: { value: 'approval' },
    })

    expect(setDefinitionYaml).not.toHaveBeenCalled()
  })

  it('blocks →approval without an executable upstream and toasts the reason', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const setDefinitionYaml = vi.fn()
    // intake 只有 start 上游：validate_approval_edges 同语义，前置校验拦下。
    render(
      <WorkflowNodeInspector
        workflow={null}
        selectedNodeKey="intake"
        definitionYaml={draftYaml}
        setDefinitionYaml={setDefinitionYaml}
        onClose={() => {}}
      />,
      { wrapper }
    )

    await screen.findByLabelText('节点执行能力')
    fireEvent.change(await screen.findByLabelText('节点类型'), {
      target: { value: 'approval' },
    })

    expect(setDefinitionYaml).not.toHaveBeenCalled()
    // 校验先于确认：前置校验都没过，就不该弹破坏性确认（P3 review）。
    expect(confirmSpy).not.toHaveBeenCalled()
    const { useUiStore } = await import('../../../stores/uiStore')
    await vi.waitFor(() =>
      expect(useUiStore.getState().toast?.message).toContain('可执行节点的入边')
    )
  })

  it('switches approval→code atomically via the capability prompt (#405)', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const promptSpy = vi
      .spyOn(window, 'prompt')
      .mockReturnValue('gate_cap' as unknown as string)
    const setDefinitionYaml = vi.fn()
    // 结构化 UI 对 approval 隐藏能力 Key 输入（loader 禁 capability）；
    // 切回 code 必须经弹窗补 capability，一次提交原子落草稿。
    render(
      <WorkflowNodeInspector
        workflow={null}
        selectedNodeKey="gate"
        definitionYaml={approvalGateYaml}
        setDefinitionYaml={setDefinitionYaml}
        onClose={() => {}}
      />,
      { wrapper }
    )

    fireEvent.change(await screen.findByLabelText('节点类型'), {
      target: { value: 'code' },
    })

    expect(promptSpy).toHaveBeenCalledWith(
      expect.stringContaining('capability')
    )
    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    const node = (
      yaml.load(nextYaml) as {
        nodes?: Record<string, Record<string, unknown>>
      }
    ).nodes?.gate
    // type 与 capability 同一次提交写入（无中间非法态）。
    expect(node?.type).toBe('code')
    expect(node?.capability).toBe('gate_cap')
    // 审批专属 config 键随切换剥除。
    expect(node).not.toHaveProperty('config')
  })

  it('keeps the draft untouched when the capability prompt is dismissed (#405)', async () => {
    const promptSpy = vi
      .spyOn(window, 'prompt')
      .mockReturnValue(null as unknown as string)
    const setDefinitionYaml = vi.fn()
    render(
      <WorkflowNodeInspector
        workflow={null}
        selectedNodeKey="gate"
        definitionYaml={approvalGateYaml}
        setDefinitionYaml={setDefinitionYaml}
        onClose={() => {}}
      />,
      { wrapper }
    )

    fireEvent.change(await screen.findByLabelText('节点类型'), {
      target: { value: 'code' },
    })

    // 取消（或留空）即放弃本次切换：草稿不动、不触发破坏性确认。
    expect(promptSpy).toHaveBeenCalled()
    expect(setDefinitionYaml).not.toHaveBeenCalled()
    expect(vi.spyOn(window, 'confirm')).not.toHaveBeenCalled()
  })

  it('skips the capability prompt when switching between executable types', async () => {
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('x')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const setDefinitionYaml = vi.fn()
    render(
      <WorkflowNodeInspector
        workflow={null}
        selectedNodeKey="intake"
        definitionYaml={draftYaml}
        setDefinitionYaml={setDefinitionYaml}
        onClose={() => {}}
      />,
      { wrapper }
    )

    // code/agent 互切本就有 capability，弹窗不该出现（直通路径不变）。
    fireEvent.change(await screen.findByLabelText('节点类型'), {
      target: { value: 'agent' },
    })

    expect(promptSpy).not.toHaveBeenCalled()
    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
  })

  it('inspector shows the DRAFT type after approval→code switch (#606 codex P2)', async () => {
    // 草稿优先：切型后 selector/sections 反映草稿类型（ghost 链形态，
    // workflow=null）。
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const setDefinitionYaml = vi.fn()
    const draftSwitched = approvalGateYaml.replace(
      '    type: approval',
      '    type: code\n    capability: gate_cap'
    )
    render(
      <WorkflowNodeInspector
        workflow={null}
        selectedNodeKey="gate"
        definitionYaml={draftSwitched}
        setDefinitionYaml={setDefinitionYaml}
        onClose={() => {}}
      />,
      { wrapper }
    )

    const selector = await screen.findByLabelText('节点类型')
    expect(selector).toHaveValue('code')
  })

  it('inspector shows the DRAFT type after a switch on a published workflow (#606 codex P2)', async () => {
    // 直击形态：已发布 workflow 的 approval 节点，草稿里已切成
    // code——header/选择器必须显示 code（草稿优先），否则受控选择器
    // 停在 approval、用户无法再选择 approval 来撤销切换。
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const setDefinitionYaml = vi.fn()
    const draftSwitched = approvalGateYaml.replace(
      '    type: approval',
      '    type: code\n    capability: gate_cap'
    )
    render(
      <WorkflowNodeInspector
        workflow={
          {
            key: 'demo',
            label: 'Demo',
            nodes: [
              {
                key: '_start',
                node_type: 'start',
                label: 'Start',
                inputs: [],
                outputs: [],
              },
              {
                key: 'gate',
                node_type: 'approval',
                label: '审批',
                inputs: [],
                outputs: [],
              },
            ],
            edges: [],
          } as never
        }
        selectedNodeKey="gate"
        definitionYaml={draftSwitched}
        setDefinitionYaml={setDefinitionYaml}
        onClose={() => {}}
      />,
      { wrapper }
    )

    const selector = await screen.findByLabelText('节点类型')
    expect(selector).toHaveValue('code')
  })

  it('falls back to the published baseline when the draft is shape-invalid (#405 codex P1)', async () => {
    // 语法合法但形状无效（edges 是 mapping 而非数组）：画布按
    // workflowYamlToDefinitionRecord 判定回退已发布 workflow；inspector
    // 草稿优先必须同一判定——否则 ghost 解析对 edges 直接 .filter()，
    // 渲染期抛异常。草稿里 gate 已切成 code，基线是 approval：回退成功
    // 的可见证据就是选择器显示基线类型 approval（而非草稿的 code）。
    const shapeBrokenDraft = approvalGateYaml
      .replace('    type: approval', '    type: code\n    capability: gate_cap')
      .replace(
        'edges:\n  - from: _start\n    to: draft_gen\n',
        'edges:\n  notalist: true\n'
      )
    render(
      <WorkflowNodeInspector
        workflow={
          {
            key: 'demo',
            label: 'Demo',
            nodes: [
              {
                key: '_start',
                node_type: 'start',
                label: 'Start',
                inputs: [],
                outputs: [],
              },
              {
                key: 'gate',
                node_type: 'approval',
                label: '审批',
                inputs: [],
                outputs: [],
              },
            ],
            edges: [],
          } as never
        }
        selectedNodeKey="gate"
        definitionYaml={shapeBrokenDraft}
        setDefinitionYaml={() => {}}
        onClose={() => {}}
      />,
      { wrapper }
    )

    // 不炸（渲染完成）且与画布一致回退到基线详情。
    const selector = await screen.findByLabelText('节点类型')
    expect(selector).toHaveValue('approval')
  })

  it('skips the prompt for an approval node that already has capability (#405 审核 P3)', async () => {
    // 手写 approval+capability 是合法瞬态——切出必须维持修复前的直通
    // 语义，不得被 prompt 拦下（cancel 会放弃合法切换）。
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('x')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const setDefinitionYaml = vi.fn()
    render(
      <WorkflowNodeInspector
        workflow={null}
        selectedNodeKey="gate"
        definitionYaml={approvalGateYaml.replace(
          '    config: {rework_target: draft_gen, feedback_artifact: review.json}',
          '    config: {rework_target: draft_gen, feedback_artifact: review.json}\n    capability: gate_cap'
        )}
        setDefinitionYaml={setDefinitionYaml}
        onClose={() => {}}
      />,
      { wrapper }
    )

    fireEvent.change(await screen.findByLabelText('节点类型'), {
      target: { value: 'code' },
    })

    expect(promptSpy).not.toHaveBeenCalled()
    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
  })

  it('renders no agent editor on a code node (#392 regression)', async () => {
    renderInspector('intake')

    await screen.findByLabelText('节点执行能力')
    // code 节点不再长出 Agent 编辑区（类型变更走头部选择器；#409 起该
    // 编辑区在 agent 节点内内联展开，无开合按钮）。
    expect(
      screen.queryByRole('button', { name: '为此 capability 新建 Agent' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '编辑 Agent' })
    ).not.toBeInTheDocument()
  })

  it('keeps Agent schema ownership inside Agent config (#406)', async () => {
    const agentYaml = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '  intake:',
      '    type: agent',
      '    label: 读取知识点',
      '    capability: intake',
      '    after: [_start]',
      '    config_schema:',
      '      properties:',
      '        ignored_node_schema:',
      '          type: boolean',
      '',
    ].join('\n')

    renderInspector('intake', { definitionYaml: agentYaml })

    expect(await screen.findByLabelText('节点执行能力')).toHaveTextContent(
      'Agent 配置'
    )
    expect(
      screen.queryByLabelText('配置 Schema intake')
    ).not.toBeInTheDocument()
  })

  it('keeps the read-only entry contract for a ghost start node', () => {
    renderInspector('_start')

    expect(screen.getByLabelText('入口节点')).toBeInTheDocument()
    expect(screen.queryByLabelText('节点名称')).not.toBeInTheDocument()
  })

  it('falls back for keys absent from both baseline and draft', () => {
    // workflow 为 null（空态）且草稿里也没有：保持「未加载 workflow」语义。
    renderInspector('no_such_node')

    expect(screen.getByText('未加载 workflow')).toBeInTheDocument()
  })
})

// 草稿 YAML 没有 start 节点：后端 loader 注入的合成 _start 不在 YAML 文本里，
// compare 把它画成 added ghost——inspector 走 compareSummary 兜底。
const yamlWithoutStart = [
  'key: demo',
  'nodes:',
  '  intake:',
  '    label: 读取知识点',
  '    capability: intake',
  '',
].join('\n')

function makeCompareSummary(
  nodeType: 'start' | 'code'
): ChangeSummaryViewModel {
  return {
    createsRevision: true,
    riskLevel: 'info',
    severityLabel: '提示',
    nodeChanges: [
      {
        type: 'added',
        nodeKey: '_start',
        label: 'Start',
        nodeType,
        fields: [],
        severity: 'info',
      },
    ],
    edgeChanges: [
      {
        type: 'added',
        source: '_start',
        target: 'intake',
        beforeCondition: null,
        afterCondition: null,
        severity: 'info',
      },
    ],
    intakeChanges: [],
    metadataChanges: [],
    riskFlags: [],
    changedNodeKeys: new Set(['_start', 'intake']),
  }
}

describe('WorkflowNodeInspector compare fallback for a synthetic start ghost', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockApi.mockResolvedValue({
      origin: 'none',
      code: '',
      has_draft: false,
      draft_code: null,
      draft_version: null,
    })
    useSettingStore.setState({ workspaceId: 'ws1' })
  })

  it('renders the editable entry contract from the compare summary', async () => {
    renderInspector('_start', {
      definitionYaml: yamlWithoutStart,
      compareSummary: makeCompareSummary('start'),
    })

    expect(await screen.findByLabelText('入口节点')).toBeInTheDocument()
    expect(screen.queryByText('未加载 workflow')).not.toBeInTheDocument()
    // 默认契约 material/ref（与 acceptedItemTypes fallback 语义一致）。
    expect(screen.getByRole('checkbox', { name: /上传文件/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /外部平台内容/ })).toBeChecked()
    expect(
      screen.getByRole('checkbox', { name: /整个文件夹/ })
    ).not.toBeChecked()
    // added 边从 edgeChanges 还原进依赖关系段。
    expect(screen.getByText('0 入 / 1 出')).toBeInTheDocument()
  })

  it('does not synthesize details for a non-start node change', () => {
    renderInspector('_start', {
      definitionYaml: yamlWithoutStart,
      compareSummary: makeCompareSummary('code'),
    })

    expect(screen.getByText('未加载 workflow')).toBeInTheDocument()
  })

  it('keeps the draft YAML resolution ahead of the compare fallback', async () => {
    // 显式 bundle 契约与合成 start 的 material/ref 缺省不同，必须采用草稿。
    renderInspector('_start', {
      definitionYaml: draftYaml.replace(
        '    type: start',
        '    type: start\n    accepted_item_types: [bundle]'
      ),
      compareSummary: makeCompareSummary('start'),
    })

    expect(await screen.findByLabelText('入口节点')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /上传文件/ })).not.toBeChecked()
    expect(
      screen.getByRole('checkbox', { name: /外部平台内容/ })
    ).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: /整个文件夹/ })).toBeChecked()
  })

  it('uses backend defaults when an explicit draft start omits its contract', async () => {
    renderInspector('_start', {
      definitionYaml: draftYaml,
      compareSummary: makeCompareSummary('start'),
    })
    expect(await screen.findByLabelText('入口节点')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /上传文件/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /外部平台内容/ })).toBeChecked()
    expect(
      screen.getByRole('checkbox', { name: /整个文件夹/ })
    ).not.toBeChecked()
  })
})
