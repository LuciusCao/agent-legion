/** #476：AgentEditor 的动态工具选项面（per-runtime 目录渲染）。
 *
 * 姊妹文件（WorkflowNodeAgentEditor.test.tsx 已近体积阈值）：这里只测
 * 目录驱动的行为——default 预选、opt-in 呈现、forced 锁定行、runtime
 * 切换的失效标记；表单生命周期用例留在原文件。
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import { AgentEditor } from './AgentEditor'

const mocks = {
  fetchAgentRuntimes: vi.fn(),
  fetchAgentDefinition: vi.fn(),
  createAgentDefinition: vi.fn(),
  saveAgentDraft: vi.fn(),
  publishAgent: vi.fn(),
  archiveAgent: vi.fn(),
}

vi.mock('../../../api', () => ({
  fetchAgentRuntimes: (...args: unknown[]) => mocks.fetchAgentRuntimes(...args),
  fetchAgentDefinition: (...args: unknown[]) =>
    mocks.fetchAgentDefinition(...args),
  createAgentDefinition: (...args: unknown[]) =>
    mocks.createAgentDefinition(...args),
  saveAgentDraft: (...args: unknown[]) => mocks.saveAgentDraft(...args),
  publishAgent: (...args: unknown[]) => mocks.publishAgent(...args),
  archiveAgent: (...args: unknown[]) => mocks.archiveAgent(...args),
}))

// 目录 fixture 与后端 catalog 契约同形（#476 契约测试钉住全等）。
function catalogResponse() {
  const entry = (name: string, tier: string, extra: object = {}) => ({
    name,
    tier,
    description: `${name} description`,
    parameters: {},
    ...extra,
  })
  return {
    runtimes: {
      pi: {
        tools: [
          entry('read', 'default'),
          entry('write', 'default'),
          entry('bash', 'default'),
        ],
      },
      velites: {
        tools: [
          entry('read', 'default'),
          entry('write', 'default'),
          entry('bash', 'default'),
          entry('uuid', 'opt-in'),
          entry('json', 'opt-in'),
          entry('validate', 'forced', { activation: '--require-output' }),
        ],
      },
    },
  }
}

function renderEditor(agentId: string | null = null) {
  return render(
    <TestQueryProvider>
      <AgentEditor
        workspaceId="ws1"
        agentId={agentId}
        initialCapability="gen"
        onSaved={() => {}}
        onChanged={() => {}}
        onArchived={() => {}}
      />
    </TestQueryProvider>
  )
}

/** 打开 Tools 下拉并返回选项映射（name → { selected, disabled }）。 */
async function openToolOptions() {
  fireEvent.mouseDown(await screen.findByLabelText('Tools'))
  const options = await screen.findAllByRole('option')
  const byName = new Map<string, { selected: boolean; disabled: boolean }>()
  for (const option of options) {
    byName.set(option.textContent ?? '', {
      selected: option.getAttribute('aria-selected') === 'true',
      disabled: option.hasAttribute('aria-disabled'),
    })
  }
  return byName
}

/** 关闭已打开的 MUI 下拉：multiple Select 点 backdrop 关闭（escape 无效）。 */
async function closeDropdown() {
  const backdrop = document.querySelector('.MuiBackdrop-root')
  if (backdrop) fireEvent.click(backdrop)
}

describe('AgentEditor tool catalog (#476)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useSettingStore.setState({ workspaceId: 'ws1' })
    mocks.fetchAgentRuntimes.mockResolvedValue(catalogResponse())
    mocks.fetchAgentDefinition.mockResolvedValue({
      latest: null,
      published: null,
    })
  })

  it('preselects the default tier and leaves opt-in unselected', async () => {
    renderEditor()
    await screen.findByText('创建草稿')
    const options = await openToolOptions()
    expect(options.get('read')?.selected).toBe(true)
    expect(options.get('write')?.selected).toBe(true)
    expect(options.get('bash')?.selected).toBe(true)
    expect(options.get('uuid（可选开启）')?.selected).toBe(false)
    await closeDropdown()
  })

  it('renders the forced tier as a locked row, not a checkbox option', async () => {
    renderEditor()
    // forced 档锁定行：validate 不进下拉选项，渲染成带激活条件的说明行。
    await screen.findByText(/validate（harness 强制，--require-output）/)
    const options = await openToolOptions()
    expect(options.has('uuid（可选开启）')).toBe(true)
    expect(
      [...options.keys()].some((name) => name.startsWith('validate'))
    ).toBe(false)
    await closeDropdown()
  })

  it('marks selected tools that the newly switched runtime does not offer', async () => {
    renderEditor()
    await screen.findByText('创建草稿')
    await openToolOptions()
    // velites 下勾选 uuid（opt-in），然后关闭下拉再切 runtime。
    fireEvent.click(screen.getByRole('option', { name: /uuid（可选开启）/ }))
    await closeDropdown()

    fireEvent.mouseDown(screen.getByLabelText('Runtime'))
    fireEvent.click(screen.getByRole('option', { name: 'pi' }))

    // 失效标记：pi 目录无 uuid——显式提示 dispatch 会拒绝，并给可点的
    // 移除 chip（codex P2 on #527：多选下拉无法取消禁用项）。
    expect(
      await screen.findByText(/已选工具不在 runtime pi 的目录里/)
    ).toBeInTheDocument()
    const removeChip = screen
      .getByText('uuid')
      .closest('div[class*="MuiChip-root"]')
    expect(removeChip).toBeInTheDocument()
    fireEvent.click(
      removeChip!.querySelector('svg[class*="MuiChip-deleteIcon"]')!
    )
    // 移除后失效提示消失，uuid 不再被选中。
    await waitFor(() =>
      expect(
        screen.queryByText(/已选工具不在 runtime pi 的目录里/)
      ).not.toBeInTheDocument()
    )
    // runtime 仍是 pi：uuid 不在选项面（也不会作为已选值残留）。
    const options = await openToolOptions()
    expect([...options.keys()].some((name) => name.startsWith('uuid'))).toBe(
      false
    )
    await closeDropdown()
  })

  it('keeps the loaded definition tools verbatim (no silent rewrite)', async () => {
    mocks.fetchAgentDefinition.mockResolvedValue({
      latest: null,
      published: {
        status: 'published',
        definition: {
          capability: 'gen',
          runtime: 'velites',
          skill: '',
          tools: ['read', 'uuid'],
        },
      },
    })
    renderEditor('agent-a')
    // 已保存值原样回填（含 opt-in 的 uuid），目录 default 预选不覆盖。
    const options = await openToolOptions()
    expect(options.get('read')?.selected).toBe(true)
    expect(options.get('uuid（可选开启）')?.selected).toBe(true)
    expect(options.get('write')?.selected).toBe(false)
    await closeDropdown()
  })

  it('sends the default-tier preselection on create when nothing is touched', async () => {
    mocks.createAgentDefinition.mockResolvedValue({ agent_id: 'gen' })
    renderEditor()
    await screen.findByText('创建草稿')
    // 目录预选落定（default 三件套全 selected）再保存。
    const options = await openToolOptions()
    expect(options.get('bash')?.selected).toBe(true)
    await closeDropdown()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '创建草稿' }))
    })
    const [, createBody] = mocks.createAgentDefinition.mock.calls[0]
    expect(createBody).toMatchObject({
      runtime: 'velites',
      tools: ['read', 'write', 'bash'],
    })
  })
})
