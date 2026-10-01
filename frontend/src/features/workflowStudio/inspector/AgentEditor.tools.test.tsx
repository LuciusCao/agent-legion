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

// #575：Tools 字段的唯一形态——「Agent 默认 / 兜底」标注（组件只内嵌
// 于节点详情，节点级「Tools 覆盖」是主入口）。
const toolsLabel = 'Tools（Agent 默认 / 兜底）'

/** 打开 Tools 下拉并返回选项映射（name → { selected, disabled }）。 */
async function openToolOptions() {
  fireEvent.mouseDown(await screen.findByLabelText(toolsLabel))
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

  // #575：Tools 字段只有「Agent 默认 / 兜底」一种形态——label 点明层级，
  // 并提示优先用节点级「Tools 覆盖」（组件唯一生产调用点是节点详情内嵌）。
  it('labels the Tools field as the Agent-level fallback default (#575)', async () => {
    renderEditor()
    await screen.findByText('创建草稿')

    expect(screen.getByLabelText(toolsLabel)).toBeInTheDocument()
    expect(
      screen.getByText('兜底默认——节点级「Tools 覆盖」优先，建议按节点覆盖')
    ).toBeInTheDocument()
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

  // #749：检查器面板的发布迁移到 CAS——expected_hash 取自草稿身份（本
  // 面板保存的响应，或详情读取里的草稿行），与聊天草稿卡同一语义；409
  // 用引导重来的专用文案（同一交互模式），不发明新 UI。
  describe('发布的 CAS 令牌（#749）', () => {
    const draftDetail = {
      latest: {
        status: 'draft',
        definition_hash: 'hash-load',
        definition: {
          capability: 'gen',
          runtime: 'velites',
          skill: '',
        },
      },
      published: null,
    }

    it('发布请求携带详情读取的草稿 definition_hash', async () => {
      mocks.fetchAgentDefinition.mockResolvedValue(draftDetail)
      mocks.publishAgent.mockResolvedValue({ status: 'published' })
      renderEditor('agent-a')
      const publish = await screen.findByRole('button', { name: '发布' })
      expect(publish).toBeEnabled()

      await act(async () => {
        fireEvent.click(publish)
      })
      expect(mocks.publishAgent).toHaveBeenCalledWith(
        'ws1',
        'agent-a',
        'hash-load'
      )
    })

    it('保存草稿后发布携带保存响应的新 hash', async () => {
      mocks.fetchAgentDefinition.mockResolvedValue(draftDetail)
      mocks.saveAgentDraft.mockResolvedValue({ definition_hash: 'hash-save-2' })
      renderEditor('agent-a')
      await screen.findByRole('button', { name: '发布' })

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))
      })
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: '发布' }))
      })
      expect(mocks.publishAgent).toHaveBeenCalledWith(
        'ws1',
        'agent-a',
        'hash-save-2'
      )
    })

    it('服务端 409（草稿被覆盖）：专用文案内联提示，无成功 toast，按钮可重试', async () => {
      mocks.fetchAgentDefinition.mockResolvedValue(draftDetail)
      mocks.publishAgent.mockRejectedValue(
        Object.assign(new Error('draft hash mismatch for agent agent-a'), {
          status: 409,
        })
      )
      const { useUiStore } = await import('../../../stores/uiStore')
      useUiStore.setState({ toast: null })
      renderEditor('agent-a')
      const publish = await screen.findByRole('button', { name: '发布' })

      await act(async () => {
        fireEvent.click(publish)
      })
      expect(screen.getByRole('alert')).toHaveTextContent(
        '草稿已被其他会话或编辑器更新，请重新打开面板从最新草稿发布'
      )
      expect(useUiStore.getState().toast).toBeNull()
      expect(screen.getByRole('button', { name: '发布' })).toBeEnabled()
    })

    // #749 修（review P2-1）：capability 占用的 409 与 CAS 拒绝共用状态码，
    // 但出路完全不同（改 capability / 归档占用者，而非「刷新重存」）——
    // 必须直显后端 detail，否则按 CAS 文案引导是误导死循环（回归到
    // #749 前的基线行为）。
    it('服务端 409（capability 被占用）：直显后端 detail，不用 CAS 文案', async () => {
      mocks.fetchAgentDefinition.mockResolvedValue(draftDetail)
      mocks.publishAgent.mockRejectedValue(
        Object.assign(
          new Error(
            "capability 'gen' is already published by Agent 'agent-b' in this workspace; exactly one published Agent per capability"
          ),
          { status: 409 }
        )
      )
      renderEditor('agent-a')
      const publish = await screen.findByRole('button', { name: '发布' })

      await act(async () => {
        fireEvent.click(publish)
      })
      const alert = screen.getByRole('alert')
      expect(alert).toHaveTextContent("capability 'gen' is already published")
      expect(alert).toHaveTextContent("Agent 'agent-b'")
      // 没有被 CAS 文案吞掉（那是误导死循环）。
      expect(alert).not.toHaveTextContent('草稿已被其他会话或编辑器更新')
    })

    // #749 修（review P3-3）：404 = 无草稿可发（刚在别处发布过），对齐
    // EntityDraftPublishButton 的可行动文案。
    it('服务端 404（无草稿可发）：可行动文案而非英文 detail', async () => {
      mocks.fetchAgentDefinition.mockResolvedValue(draftDetail)
      mocks.publishAgent.mockRejectedValue(
        Object.assign(new Error('no draft for agent agent-a'), { status: 404 })
      )
      renderEditor('agent-a')
      const publish = await screen.findByRole('button', { name: '发布' })

      await act(async () => {
        fireEvent.click(publish)
      })
      expect(screen.getByRole('alert')).toHaveTextContent(
        '没有待发布的草稿（可能刚已发布过）'
      )
    })

    it('无草稿身份（definition_hash 缺失）时发布按钮禁用', async () => {
      // 异常形态：draft 行存在但响应缺 hash——无 CAS 令牌不发布（与聊天
      // 卡 codex P1 第四轮同一立场：无法验证身份的发布会静默发别人的内容）。
      mocks.fetchAgentDefinition.mockResolvedValue({
        latest: {
          status: 'draft',
          definition: { capability: 'gen', runtime: 'velites', skill: '' },
        },
        published: null,
      })
      renderEditor('agent-a')
      expect(await screen.findByRole('button', { name: '发布' })).toBeDisabled()
    })
  })
})
