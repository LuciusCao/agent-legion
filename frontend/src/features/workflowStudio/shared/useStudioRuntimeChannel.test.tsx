import { renderHook, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useStudioRuntimeChannel } from './useStudioRuntimeChannel'
import { useUiStore } from '../../../stores/uiStore'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'

const mocks = {
  save: vi.fn(),
  rollback: vi.fn(),
  fetchActive: vi.fn(),
}

vi.mock('../../../api', () => ({
  saveWorkflowRuntimeConfig: (...args: unknown[]) => mocks.save(...args),
  rollbackWorkflowRuntimeConfig: (...args: unknown[]) =>
    mocks.rollback(...args),
  fetchActiveWorkflowRevision: (...args: unknown[]) =>
    mocks.fetchActive(...args),
}))

const BASELINE = `key: ws1
label: demo
nodes:
  write_script:
    type: agent
    label: 撰写脚本
    capability: write_script
    execution:
      runtime: velites
      model: gpt-5.1
`

const FRESH_ACTIVE = {
  // canonical 重建（键序重排 + 新增 runtime 值）——与草稿原文纯文本不等。
  definition_yaml: `label: demo
key: ws1
nodes:
  write_script:
    capability: write_script
    label: 撰写脚本
    type: agent
    execution:
      runtime: velites
      model: gpt-5.2
`,
  revision: { id: 'rev1', version: 1 },
  workflow: null,
}

function setup(args: {
  draftYaml: string
  originalYaml?: string
  setDraftYaml?: (value: string) => void
}) {
  const queryClient = new QueryClient()
  queryClient.setQueryData(extraQueryKeys.workflowStudioData('ws1'), {
    active: { definition_yaml: args.originalYaml ?? BASELINE },
    history: { revisions: [] },
    workspaces: [],
  })
  const setDraftYaml = args.setDraftYaml ?? vi.fn()
  const markRuntimeChannelSaved = vi.fn()
  const view = renderHook(
    () =>
      useStudioRuntimeChannel({
        workspaceId: 'ws1',
        originalYaml: args.originalYaml ?? BASELINE,
        draftYaml: args.draftYaml,
        setDraftYaml,
        markRuntimeChannelSaved,
      }),
    {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    }
  )
  return {
    ...view,
    queryClient,
    setDraftYaml,
    markRuntimeChannelSaved,
  }
}

describe('useStudioRuntimeChannel（M48 运行通道）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.save.mockResolvedValue({
      revision: FRESH_ACTIVE.revision,
      runtime_version: {
        version: 1,
        revision_id: 'rev1',
        changed_keys: ['nodes.write_script.model'],
        created_by: 'user:1',
        created_at: '2026-10-21T10:00:00',
      },
    })
    mocks.rollback.mockResolvedValue({
      revision: FRESH_ACTIVE.revision,
      runtime_version: {
        version: 2,
        revision_id: 'rev1',
        changed_keys: ['nodes.write_script.model'],
        created_by: 'user:1',
        created_at: '2026-10-21T11:00:00',
      },
    })
    mocks.fetchActive.mockResolvedValue(FRESH_ACTIVE)
  })

  it('保存请求体 = 基线结构 + 草稿 runtime 切片（草稿的结构改动不进请求）', async () => {
    const draft = BASELINE.replace(
      'label: 撰写脚本',
      'label: 撰写脚本（精修版）'
    ).replace('model: gpt-5.1', 'model: gpt-5.2')
    const { result, queryClient } = setup({ draftYaml: draft })

    let ok = false
    await act(async () => {
      ok = await result.current.saveRuntimeConfig()
    })

    expect(ok).toBe(true)
    const [workspaceId, body] = mocks.save.mock.calls[0]
    expect(workspaceId).toBe('ws1')
    expect(body).toContain('model: gpt-5.2')
    expect(body).not.toContain('精修版')
    expect(useUiStore.getState().toast?.type).toBe('success')
    // 查询缓存的 active 被新基线替换
    const cached = queryClient.getQueryData(
      extraQueryKeys.workflowStudioData('ws1')
    ) as { active: { definition_yaml: string } }
    expect(cached.active.definition_yaml).toBe(FRESH_ACTIVE.definition_yaml)
  })

  it('无结构编辑：草稿对齐新 canonical 基线', async () => {
    const draft = BASELINE.replace('model: gpt-5.1', 'model: gpt-5.2')
    const { result, setDraftYaml, markRuntimeChannelSaved } = setup({
      draftYaml: draft,
    })

    await act(async () => {
      await result.current.saveRuntimeConfig()
    })

    expect(markRuntimeChannelSaved).toHaveBeenCalled()
    expect(setDraftYaml).toHaveBeenCalledWith(FRESH_ACTIVE.definition_yaml)
  })

  it('有结构编辑：保留草稿结构、并入生效后的 runtime 切片（回滚场景）', async () => {
    // 草稿：结构改动（label）+ runtime 改动（model gpt-5.2）；回滚后
    // active 的 runtime 回到 gpt-5.1——草稿结构保留、runtime 并入新值。
    mocks.fetchActive.mockResolvedValue({
      ...FRESH_ACTIVE,
      definition_yaml: FRESH_ACTIVE.definition_yaml.replace(
        'model: gpt-5.2',
        'model: gpt-5.1'
      ),
    })
    const draft = BASELINE.replace(
      'label: 撰写脚本',
      'label: 撰写脚本（精修版）'
    ).replace('model: gpt-5.1', 'model: gpt-5.2')
    const { result, setDraftYaml } = setup({ draftYaml: draft })

    await act(async () => {
      await result.current.rollbackRuntimeConfig(1)
    })

    const nextDraft = (setDraftYaml as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as string
    expect(nextDraft).toContain('精修版')
    expect(nextDraft).toContain('model: gpt-5.1')
    expect(nextDraft).not.toContain('model: gpt-5.2')
  })

  it('runtime 切片差异驱动 runtimeChangedKeys', () => {
    const draft = BASELINE.replace('model: gpt-5.1', 'model: gpt-5.2')
    const { result } = setup({ draftYaml: draft })
    expect(result.current.runtimeChangedKeys).toEqual([
      'nodes.write_script.model',
    ])
  })

  it('结构改动不计入 runtimeChangedKeys', () => {
    const draft = BASELINE.replace('label: 撰写脚本', 'label: 改名')
    const { result } = setup({ draftYaml: draft })
    expect(result.current.runtimeChangedKeys).toEqual([])
  })

  it('409 冲突报错误 toast 且不改草稿', async () => {
    mocks.save.mockRejectedValue(
      Object.assign(new Error('conflict'), { status: 409 })
    )
    const draft = BASELINE.replace('model: gpt-5.1', 'model: gpt-5.2')
    const { result, setDraftYaml } = setup({ draftYaml: draft })

    let ok = true
    await act(async () => {
      ok = await result.current.saveRuntimeConfig()
    })

    expect(ok).toBe(false)
    expect(useUiStore.getState().toast?.type).toBe('error')
    expect(useUiStore.getState().toast?.message).toContain('刷新后重试')
    expect(setDraftYaml).not.toHaveBeenCalled()
  })

  it('回滚调 POST 并按相同路径收尾', async () => {
    const { result } = setup({ draftYaml: BASELINE })

    let ok = false
    await act(async () => {
      ok = await result.current.rollbackRuntimeConfig(3)
    })

    expect(ok).toBe(true)
    expect(mocks.rollback).toHaveBeenCalledWith('ws1', 3)
    expect(useUiStore.getState().toast?.message).toContain('v3')
  })

  it('agent 建议场景：runtime 来源可取建议 YAML 而非当前草稿', async () => {
    const proposal = BASELINE.replace('model: gpt-5.1', 'model: gpt-5.3')
    const { result } = setup({ draftYaml: BASELINE })

    await act(async () => {
      await result.current.saveRuntimeConfig(proposal)
    })

    const [, body] = mocks.save.mock.calls[0]
    expect(body).toContain('model: gpt-5.3')
  })

  it('M49 连续编辑：两次「应用并生效」各发一次 PATCH（每次生效一条配置版本）', async () => {
    // agent 会话内连续调整 runtime：第一次保存落定后守卫必须复位，第二
    // 次建议照常发出——后端逐次追加配置版本（锁内串行，M47），客户端不吞。
    const proposalA = BASELINE.replace('model: gpt-5.1', 'model: gpt-5.2')
    const proposalB = BASELINE.replace('model: gpt-5.1', 'model: gpt-5.3')
    const { result } = setup({ draftYaml: BASELINE })

    await act(async () => {
      await result.current.saveRuntimeConfig(proposalA)
    })
    await act(async () => {
      await result.current.saveRuntimeConfig(proposalB)
    })

    expect(mocks.save).toHaveBeenCalledTimes(2)
    expect(mocks.save.mock.calls[0][1]).toContain('model: gpt-5.2')
    expect(mocks.save.mock.calls[1][1]).toContain('model: gpt-5.3')
  })

  it('M49 在途守卫（ref 级）：第一次 PATCH 未落定时第二次调用立即拒发', async () => {
    // state 守卫读闭包快照，同一渲染周期内的第二次调用会穿透（建议卡双击
    // / 卡片与 Inspector 保存按钮同帧）；ref 守卫同步置位，保证「一次生效
    // = 一次 PATCH = 一条配置版本」，用户无感知的多版本连发被挡在客户端。
    let releaseSave: (() => void) | null = null
    mocks.save.mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseSave = () =>
            resolve({
              revision: FRESH_ACTIVE.revision,
              runtime_version: {
                version: 1,
                revision_id: 'rev1',
                changed_keys: ['nodes.write_script.model'],
                created_by: 'user:1',
                created_at: '2026-10-21T10:00:00',
              },
            })
        })
    )
    const draft = BASELINE.replace('model: gpt-5.1', 'model: gpt-5.2')
    const { result } = setup({ draftYaml: draft })

    let firstPromise!: Promise<boolean>
    act(() => {
      firstPromise = result.current.saveRuntimeConfig()
    })
    // 第一次 PATCH 在途：第二次不进入 try（setState 不会触发），直接 false。
    const second = await result.current.saveRuntimeConfig()
    expect(second).toBe(false)
    expect(mocks.save).toHaveBeenCalledTimes(1)

    await act(async () => {
      releaseSave?.()
      expect(await firstPromise).toBe(true)
    })
    // 落定后守卫复位，后续保存恢复可用（mock 恢复正常 resolve 形态）。
    mocks.save.mockResolvedValue({
      revision: FRESH_ACTIVE.revision,
      runtime_version: {
        version: 2,
        revision_id: 'rev1',
        changed_keys: ['nodes.write_script.model'],
        created_by: 'user:1',
        created_at: '2026-10-21T10:05:00',
      },
    })
    await act(async () => {
      await result.current.saveRuntimeConfig()
    })
    expect(mocks.save).toHaveBeenCalledTimes(2)
  })

  it('r1 P2-1：保存已生效但本地刷新失败——报「已生效 + 手动刷新」而非保存失败', async () => {
    mocks.fetchActive.mockRejectedValue(new Error('network down'))
    const draft = BASELINE.replace('model: gpt-5.1', 'model: gpt-5.2')
    const { result } = setup({ draftYaml: draft })

    let ok = false
    await act(async () => {
      ok = await result.current.saveRuntimeConfig()
    })

    expect(ok).toBe(true)
    const toast = useUiStore.getState().toast
    expect(toast?.message).toContain('已保存并生效')
    expect(toast?.message).toContain('手动刷新')
    expect(toast?.message).not.toContain('保存失败')
  })

  it('r1 P2-1：回滚已生效但本地刷新失败同样不误报回滚失败', async () => {
    mocks.fetchActive.mockRejectedValue(new Error('network down'))
    const { result } = setup({ draftYaml: BASELINE })

    let ok = false
    await act(async () => {
      ok = await result.current.rollbackRuntimeConfig(3)
    })

    expect(ok).toBe(true)
    expect(useUiStore.getState().toast?.message).toContain(
      '已回滚到运行配置 v3'
    )
    expect(useUiStore.getState().toast?.message).toContain('手动刷新')
  })
})
