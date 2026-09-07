/** #443/#476：agent 节点的节点级 tools 声明编辑入口（组件）。 */

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

function renderEditor(definitionYaml: string, setDefinitionYaml = vi.fn()) {
  return {
    setDefinitionYaml,
    ...render(
      <TestQueryProvider>
        <WorkflowNodeToolsEditor
          node={node}
          runtime="velites"
          definitionYaml={definitionYaml}
          setDefinitionYaml={setDefinitionYaml}
        />
      </TestQueryProvider>
    ),
  }
}

/** 打开下拉前等目录加载（disabled 消失），再 mouseDown 打开。 */
async function openToolsMenu() {
  const label = '工具声明（空 = 跟随 Agent 定义）'
  await screen.findByLabelText(label)
  await waitFor(() => expect(screen.getByLabelText(label)).not.toBeDisabled())
  fireEvent.mouseDown(screen.getByLabelText(label))
}

describe('WorkflowNodeToolsEditor (#443/#476)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchAgentRuntimes.mockResolvedValue(catalogResponse())
  })

  it('shows the undeclared state as empty (follows the Agent definition)', async () => {
    renderEditor('nodes:\n  gen:\n    type: agent\n')
    const field = await screen.findByLabelText(
      '工具声明（空 = 跟随 Agent 定义）'
    )
    expect(field).toBeInTheDocument()
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
