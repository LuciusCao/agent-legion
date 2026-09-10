/** #443/#476：agent 节点的节点级 tools 声明编辑入口（组件）。
 *  #575：主入口形态——label 点明覆盖层级，未声明时 helperText 展示
 *  解析后的生效值（Agent 默认兜底）。 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowNodeToolsEditor } from './WorkflowNodeToolsEditor'

const mocks = { fetchAgentRuntimes: vi.fn() }

vi.mock('../../../api', () => ({
  fetchAgentRuntimes: (...args: unknown[]) => mocks.fetchAgentRuntimes(...args),
}))

const node = {
  key: 'gen',
  node_type: 'agent',
  capability: 'gen',
  inputs: [],
  outputs: ['out/a.md'],
} as unknown as WorkflowNodeRecord

function catalogResponse() {
  const entry = (name: string, tier: string, extra: object = {}) => ({
    name,
    tier,
    description: '',
    parameters: {},
    ...extra,
  })
  return {
    runtimes: {
      pi: { tools: [entry('read', 'default'), entry('bash', 'default')] },
      velites: {
        tools: [
          entry('read', 'default'),
          entry('write', 'default'),
          entry('uuid', 'opt-in'),
          entry('json', 'opt-in'),
          entry('validate', 'forced', { activation: '--require-output' }),
        ],
      },
    },
  }
}

function renderEditor(
  definitionYaml: string,
  setDefinitionYaml = vi.fn(),
  agentDefaultTools?: string[]
) {
  return {
    setDefinitionYaml,
    ...render(
      <TestQueryProvider>
        <WorkflowNodeToolsEditor
          node={node}
          runtime="velites"
          agentDefaultTools={agentDefaultTools}
          definitionYaml={definitionYaml}
          setDefinitionYaml={setDefinitionYaml}
        />
      </TestQueryProvider>
    ),
  }
}

const toolsLabel = 'Tools 覆盖（留空 = 跟随 Agent 默认）'

/** 打开下拉前等目录加载（disabled 消失），再 mouseDown 打开。 */
async function openToolsMenu() {
  await screen.findByLabelText(toolsLabel)
  await waitFor(() =>
    expect(screen.getByLabelText(toolsLabel)).not.toBeDisabled()
  )
  fireEvent.mouseDown(screen.getByLabelText(toolsLabel))
}

describe('WorkflowNodeToolsEditor (#443/#476/#575)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchAgentRuntimes.mockResolvedValue(catalogResponse())
  })

  it('shows the undeclared state with the followed Agent defaults as the effective-value hint', async () => {
    // #575：未声明不再是裸空态——helperText 展示解析后的生效值与来源。
    renderEditor('nodes:\n  gen:\n    type: agent\n', vi.fn(), [
      'read',
      'write',
      'bash',
    ])
    const field = await screen.findByLabelText(toolsLabel)
    expect(field).toBeInTheDocument()
    expect(
      screen.getByText('当前生效（跟随 Agent 默认）：read, write, bash')
    ).toBeInTheDocument()
  })

  it('notes the empty effective value when the Agent definition declares no tools', async () => {
    renderEditor('nodes:\n  gen:\n    type: agent\n', vi.fn(), [])
    await screen.findByLabelText(toolsLabel)
    expect(
      screen.getByText('当前生效（跟随 Agent 默认）：（空）')
    ).toBeInTheDocument()
  })

  it('hides the fallback hint once the node declares its own tools', async () => {
    renderEditor(
      'nodes:\n  gen:\n    type: agent\n    tools:\n      - read\n',
      vi.fn(),
      ['read', 'write', 'bash']
    )
    await screen.findByLabelText(toolsLabel)
    expect(
      screen.queryByText(/当前生效（跟随 Agent 默认）/)
    ).not.toBeInTheDocument()
  })

  it('patches the YAML declaration on selection', async () => {
    const setDefinitionYaml = vi.fn()
    const yaml = 'nodes:\n  gen:\n    type: agent\n'
    renderEditor(yaml, setDefinitionYaml)
    await openToolsMenu()
    fireEvent.click(
      await screen.findByRole('option', { name: /uuid（可选开启）/ })
    )
    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    const [nextYaml] = setDefinitionYaml.mock.calls[0]
    expect(nextYaml).toContain('tools:')
    expect(nextYaml).toContain('- uuid')
  })

  it('renders forced-tier tools nowhere in the option list', async () => {
    renderEditor('nodes:\n  gen:\n    type: agent\n')
    await openToolsMenu()
    const options = await screen.findAllByRole('option')
    const names = options.map((option) => option.textContent)
    expect(names.some((name) => (name ?? '').startsWith('validate'))).toBe(
      false
    )
  })

  it('offers removal for declared tools the runtime does not offer', async () => {
    // velites 目录无 bash：已声明 bash 的节点切到 velites 后给可点的
    // 移除 chip（codex P2 on #527：多选下拉无法取消禁用项），点击剔除。
    const setDefinitionYaml = vi.fn()
    renderEditor(
      'nodes:\n  gen:\n    type: agent\n    tools:\n      - bash\n',
      setDefinitionYaml
    )
    const alerts = await screen.findAllByText((_, element) => {
      if (element?.getAttribute('role') !== 'alert') return false
      const text = (element.textContent ?? '').replace(/\s+/g, '')
      return text.includes('已声明工具不在runtimevelites的目录里')
    })
    expect(alerts.length).toBeGreaterThan(0)
    const chip = screen.getByText('bash').closest('div[class*="MuiChip-root"]')
    fireEvent.click(chip!.querySelector('svg[class*="MuiChip-deleteIcon"]')!)
    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    // bash 被剔除，tools 键整体移除（回到未声明）。
    expect(nextYaml).not.toContain('tools:')
  })
})
