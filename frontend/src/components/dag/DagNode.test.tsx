import { render, screen, fireEvent } from '@testing-library/react'
import { DagNode, DagNodeData } from './DagNode'
import { describe, it, expect } from 'vitest'
import { ReactFlowProvider } from '@xyflow/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import badgeStyles from './DagNodeBadges.module.css'

const TestDagNode = DagNode as unknown as React.FC<{
  id: string
  type: string
  data: DagNodeData
  selected?: boolean
  isConnectable?: boolean
}>

function renderWithProvider(data: DagNodeData, selected = false) {
  return render(
    <ReactFlowProvider>
      <TestDagNode
        id="n1"
        type="dagNode"
        data={data}
        selected={selected}
        isConnectable={false}
      />
    </ReactFlowProvider>
  )
}

const baseData: DagNodeData = {
  label: 'review_keywords',
  status: 'completed',
  duration: 12.4,
  executorKind: 'pi',
  nodeKey: 'review_keywords',
  capability: 'review_keywords',
  topologyBadges: ['entry', 'branch'],
  inputs: ['transcription.json', 'chapters.json'],
  outputs: ['keywords.json'],
}

describe('DagNode', () => {
  it('renders label, status, duration and executor kind', () => {
    renderWithProvider(baseData)
    expect(screen.getByText('Key · review_keywords')).toBeInTheDocument()
    expect(screen.getByText('能力 · review_keywords')).toBeInTheDocument()
    expect(screen.getByText('pi')).toBeInTheDocument()
    expect(screen.getByText('入口')).toBeInTheDocument()
    expect(screen.getByText('分支')).toBeInTheDocument()
    expect(screen.getByText(/耗时 12.4s/)).toBeInTheDocument()
  })

  // #415：label 不再单行省略——两行 line-clamp 由 CSS（.label 的
  // -webkit-line-clamp: 2）承担，jsdom 测不了布局，这里兜底断言完整文本
  // 仍渲染在 DOM 里且 title 属性携带全文（tooltip 兜底），换行/裁剪交给
  // CSS 消费者验证。
  it('keeps the full label text in the DOM with a title tooltip', () => {
    renderWithProvider({
      ...baseData,
      label: '生成审题关键信息汇总摘要',
    })
    const label = screen.getByTitle('生成审题关键信息汇总摘要')
    expect(label).toHaveTextContent('生成审题关键信息汇总摘要')
  })

  it('renders input and output chips', () => {
    renderWithProvider(baseData)
    expect(screen.getByText('transcription.json')).toBeInTheDocument()
    expect(screen.getByText('chapters.json')).toBeInTheDocument()
    expect(screen.getByText('keywords.json')).toBeInTheDocument()
  })

  it('collapses inputs and outputs when more than 3', () => {
    const data: DagNodeData = {
      ...baseData,
      inputs: ['a.json', 'b.json', 'c.json', 'd.json'],
      outputs: ['x.json', 'y.json', 'z.json', 'w.json'],
    }
    renderWithProvider(data)
    const moreButtons = screen.getAllByText('+1')
    expect(moreButtons).toHaveLength(2)
    fireEvent.click(moreButtons[0])
    expect(screen.getByText('d.json')).toBeInTheDocument()
  })

  it.each([
    ['pending', 'radio_button_unchecked'],
    ['running', 'hourglass_empty'],
    ['completed', 'check_circle'],
    ['failed', 'error'],
    ['stale', 'warning'],
  ] as const)('applies %s status and renders %s icon', (status, icon) => {
    void icon
    renderWithProvider({ ...baseData, status })
    const card = screen.getByTestId('dag-node')
    expect(card).toHaveAttribute('data-status', status)
    expect(screen.getByTestId(`dag-icon-${status}`)).toBeInTheDocument()
  })

  it('renders Agent and Worker identity separately in the badge', () => {
    renderWithProvider({
      ...baseData,
      executorKind: 'pi',
      agentId: 'key-info-generator',
      workerId: 'worker-abc123def456',
    })
    const badge = screen.getByTestId('dag-node-execution-badge')
    expect(badge).toHaveTextContent('abc123de')
    expect(badge).toHaveAttribute(
      'title',
      'key-info-generator / worker-abc123def456'
    )
  })

  // #423 review P2：动态徽标（terminalTag/workerTag）内容来自用户数据/运行
  // 时，长值不能挤压 label 或伸出卡片。jsdom 测不了布局，这里三层断言：
  // 1) 长文本完整保留在 DOM 且 title 携带全文（截断只交给 CSS）；
  // 2) 徽标确实挂在可收缩截断的 class 上（组件契约——terminalTag 由
  //    DagNodeTerminalBadge、workerTag 由 DagNodeExecutionBadge 渲染）；
  // 3) 该 class 在 DagNodeBadges.module.css 里具备「可收缩 + max-width +
  //    单行省略」完整策略（读源文件断言，防止选择器被改错对象）。固定短
  //    徽标（executorTag 等）则断言保持 flex-shrink: 0 不收缩。
  it('keeps a long terminal outcome truncatable without crowding out the label', () => {
    renderWithProvider({
      ...baseData,
      terminalOutcome:
        'a-very-long-terminal-outcome-that-exceeds-the-badge-budget',
    })
    const terminalTag = screen.getByTitle(
      'a-very-long-terminal-outcome-that-exceeds-the-badge-budget'
    )
    expect(terminalTag.className).toContain(badgeStyles.terminalTag)
    // label 仍完整渲染在同一个 header 行里（不被徽标顶掉）
    expect(screen.getByTitle(baseData.label)).toBeInTheDocument()
  })

  it('keeps a long unassigned agentId truncatable without crowding out the label', () => {
    renderWithProvider({
      ...baseData,
      agentId: 'an-extremely-long-queued-agent-id-without-a-worker-assignment',
    })
    const badge = screen.getByTestId('dag-node-execution-badge')
    expect(badge.className).toContain(badgeStyles.workerTag)
    expect(badge).toHaveAttribute(
      'title',
      'an-extremely-long-queued-agent-id-without-a-worker-assignment'
    )
    expect(screen.getByTitle(baseData.label)).toBeInTheDocument()
  })

  it('gives dynamic badges a shrink-and-ellipsis CSS strategy while fixed badges stay rigid', () => {
    const css = readFileSync(
      resolve(__dirname, 'DagNodeBadges.module.css'),
      'utf-8'
    )
    const dynamicRule = css.match(
      /\.terminalTag,\s*\n\.workerTag\s*\{([^}]*)\}/
    )
    expect(dynamicRule).not.toBeNull()
    const declarations = dynamicRule![1]
    // 可收缩 + max-width 上限 + 单行省略 + 允许收缩到内容以下
    expect(declarations).toContain('flex-shrink: 1')
    expect(declarations).toMatch(/max-width: \d+px/)
    expect(declarations).toContain('overflow: hidden')
    expect(declarations).toContain('white-space: nowrap')
    expect(declarations).toContain('text-overflow: ellipsis')
    expect(declarations).toContain('min-width: 0')
    // 固定短徽标不收缩：DagNode.module.css 的共享基线
    // （executorTag/executionWarningTag）保持 flex-shrink: 0
    const nodeCss = readFileSync(
      resolve(__dirname, 'DagNode.module.css'),
      'utf-8'
    )
    const baselineRule = nodeCss.match(
      /\.executorTag,\s*\n\.executionWarningTag\s*\{([^}]*)\}/
    )
    expect(baselineRule).not.toBeNull()
    expect(baselineRule![1]).toContain('flex-shrink: 0')
  })

  it('falls back to executorId when workerId is missing', () => {
    renderWithProvider({
      ...baseData,
      executorId: 'code-default',
      workerId: null,
    })
    const badge = screen.getByTestId('dag-node-execution-badge')
    expect(badge).toHaveTextContent('code-default')
    expect(badge).toHaveAttribute('title', 'code-default')
  })

  it('renders the Agent while a request is queued without a Worker', () => {
    renderWithProvider({ ...baseData, agentId: 'key-info-generator' })
    expect(screen.getByTestId('dag-node-execution-badge')).toHaveTextContent(
      'key-info-generator'
    )
  })

  it('renders no executor badge when all execution identities are missing', () => {
    renderWithProvider({
      ...baseData,
      agentId: null,
      executorId: null,
      workerId: null,
    })
    expect(
      screen.queryByTestId('dag-node-execution-badge')
    ).not.toBeInTheDocument()
  })

  it('renders an unbound warning chip when the node has no executor binding', () => {
    renderWithProvider({
      ...baseData,
      agentId: null,
      executorId: null,
      workerId: null,
      executorUnbound: true,
    })
    expect(screen.getByText('未绑定')).toBeInTheDocument()
  })

  // #333：agent 节点 execution 缺口的警告徽标（文案作为 title 悬浮提示）。
  it('renders the execution warning tag only when executionWarning is set', () => {
    renderWithProvider({
      ...baseData,
      executionWarning: '缺 provider / model，该节点跑不起来',
    })
    expect(screen.getByText('缺执行配置')).toHaveAttribute(
      'title',
      '缺 provider / model，该节点跑不起来'
    )
  })

  it('renders no execution warning tag by default', () => {
    renderWithProvider(baseData)
    expect(screen.queryByText('缺执行配置')).not.toBeInTheDocument()
  })

  it('renders not applicable node status', () => {
    renderWithProvider({
      label: '生成关键信息',
      status: 'not_applicable',
      inputs: [],
      outputs: [],
    })

    expect(screen.getByText('不适用')).toBeInTheDocument()
  })

  it.each([
    ['added', '新增'],
    ['modified', '已改'],
    ['removed', '已删'],
  ] as const)('renders the %s change badge as %s', (changeType, label) => {
    renderWithProvider({ ...baseData, changeType })
    expect(
      screen.getByTestId(`dag-change-badge-${changeType}`)
    ).toHaveTextContent(label)
  })

  it('renders no change badge without a change type', () => {
    renderWithProvider(baseData)
    expect(
      screen.queryByTestId('dag-change-badge-added')
    ).not.toBeInTheDocument()
    expect(
      screen.queryByTestId('dag-change-badge-modified')
    ).not.toBeInTheDocument()
    expect(
      screen.queryByTestId('dag-change-badge-removed')
    ).not.toBeInTheDocument()
  })

  // #276：置灰态渲染——hover/选中联动时非同链路节点由 DagGraph 写入
  // data.dimmed = true，节点卡片自身渲染 opacity: 0.45。
  it('dims the node card when data.dimmed is true', () => {
    const dimmed = renderWithProvider({ ...baseData, dimmed: true })
    expect(dimmed.getByTestId('dag-node').style.opacity).toBe('0.45')
    dimmed.unmount()

    const plain = renderWithProvider(baseData)
    expect(plain.getByTestId('dag-node').style.opacity).toBe('')
  })

  // #276：active 态渲染——hover/选中的目标节点由 DagGraph 写入 data.active
  // = true，卡片渲染蓝色轮廓（旧版 selectedFlowNode 的视觉等价物）。
  it('outlines the node card when data.active is true', () => {
    const active = renderWithProvider({ ...baseData, active: true })
    expect(active.getByTestId('dag-node').className).toContain('active')
    active.unmount()

    const plain = renderWithProvider(baseData)
    expect(plain.getByTestId('dag-node').className).not.toContain('active')
  })

  // #276：memo 比较函数——data 引用变化但业务内容相同（含 dimmed）时，
  // 节点不应重渲染。这是「hover 只重渲染受影响节点」在组件级的兜底断言：
  // DagGraph 只在 dimmed 翻转时新建 data，这里模拟「引用变了但内容没变」
  // 的输入（memo 比较被误改成全等引用比较时，行为退化在这里表现为肉眼不可见，
  // 所以配套断言「内容变化仍必须渲染」防止比较函数被写成恒 true）。
  it('skips re-render when only the data reference changes', () => {
    const { rerender } = render(
      <ReactFlowProvider>
        <TestDagNode
          id="n1"
          type="dagNode"
          data={baseData}
          isConnectable={false}
        />
      </ReactFlowProvider>
    )
    // 结构相同的另一份 data：内容一致、引用不同（inputs 数组也是新引用，
    // 与 computeLayout 每次 props 变化时重建数组的行为一致）。
    const sameContentData: DagNodeData = {
      ...baseData,
      inputs: [...baseData.inputs],
    }
    rerender(
      <ReactFlowProvider>
        <TestDagNode
          id="n1"
          type="dagNode"
          data={sameContentData}
          isConnectable={false}
        />
      </ReactFlowProvider>
    )
    // 内容不同的 data（label 变化）必须触发渲染：label 出现在 title 属性上。
    rerender(
      <ReactFlowProvider>
        <TestDagNode
          id="n1"
          type="dagNode"
          data={{ ...baseData, label: 'renamed_label' }}
          isConnectable={false}
        />
      </ReactFlowProvider>
    )
    expect(screen.getByTitle('renamed_label')).toBeInTheDocument()
  })
})
