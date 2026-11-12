/* 自动校验主题用例（#804 定案，自 useWorkflowStudioActions.test.ts 拆出——
 * 测试文件 800 行纪律，用例零改动迁移；mock/setup 与原文件同构）。
 * 发布主题用例（发布门控/冲突/reviewStale/publish 通道）留在原文件。 */
import { renderHook, waitFor } from '@testing-library/react'
import { act } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useWorkflowStudioActions } from './useWorkflowStudioActions'
import { VALIDATION_TRIGGER_DELAY_MS } from './draftAutoValidationRunner'
import { useUiStore } from '../../../stores/uiStore'
import type { DraftSaveStatus } from './draftSaveTypes'
import type { UseWorkflowStudioDraftResult } from './useWorkflowStudioDraft'
import type { UseWorkflowDraftCompareResult } from './useWorkflowDraftCompare'

const mocks = {
  publishWorkflowDraft: vi.fn(),
  validateWorkflowDraft: vi.fn(),
}

vi.mock('../../../api', () => ({
  fetchAgentRuntimes: vi.fn(() => Promise.resolve({ runtimes: {} })),
  publishWorkflowDraft: (...args: unknown[]) =>
    mocks.publishWorkflowDraft(...args),
  validateWorkflowDraft: (...args: unknown[]) =>
    mocks.validateWorkflowDraft(...args),
}))

type DraftWithSave = UseWorkflowStudioDraftResult & {
  draftSave: { status: DraftSaveStatus; savedAt: string | null }
  invalidateServerDraft: () => Promise<unknown>
}

const draft: DraftWithSave = {
  draftYaml: 'key: demo\n',
  setDraftYaml: vi.fn(),
  definitionYaml: 'key: demo\n',
  visibleWorkflow: null,
  visibleRevision: null,
  readOnly: false,
  dirty: true,
  canSubmit: true,
  viewMode: 'draft',
  selectedRevisionId: null,
  hasPreservedDraft: false,
  isLoadingRevision: false,
  revisionLoadError: null,
  draftSave: { status: 'idle', savedAt: null },
  markDraftPublished: vi.fn(),
  markRuntimeChannelSaved: vi.fn(),
  invalidateServerDraft: vi.fn(() => Promise.resolve()),
  selectRevision: vi.fn(),
  backToDraft: vi.fn(),
  useViewedRevisionAsDraft: vi.fn(),
}

const compare: UseWorkflowDraftCompareResult = {
  compareState: 'idle',
  compareResponse: null,
  compareErrors: null,
  compareSummary: null,
  retry: vi.fn(),
}

const compareWithChanges: UseWorkflowDraftCompareResult = {
  compareState: 'ready',
  compareResponse: null,
  compareErrors: null,
  compareSummary: {
    createsRevision: true,
    riskLevel: 'info',
    severityLabel: '提示',
    nodeChanges: [
      {
        type: 'modified',
        nodeKey: 'a',
        label: 'A',
        nodeType: 'code',
        fields: [],
        severity: 'info',
      },
    ],
    edgeChanges: [],
    intakeChanges: [],
    metadataChanges: [],
    riskFlags: [],
    changedNodeKeys: new Set(['a']),
  },
  retry: vi.fn(),
}

const reload = vi.fn().mockResolvedValue(undefined)

type AutoProps = {
  saveStatus: DraftSaveStatus
  definitionYaml: string
  canSubmit?: boolean
  conflict?: boolean
}

function renderActionsHook(initial: AutoProps) {
  return renderHook(
    ({ saveStatus, definitionYaml, canSubmit, conflict }: AutoProps) =>
      useWorkflowStudioActions(
        'ws1',
        {
          ...draft,
          definitionYaml,
          canSubmit: canSubmit ?? true,
          draftSave: { status: saveStatus, savedAt: null, conflict },
        },
        reload,
        compare
      ),
    { initialProps: initial }
  )
}

/** 驱动一次「保存成功」边沿：idle → saved。 */
async function flushSaved(
  rerender: (props: AutoProps) => void,
  props: AutoProps
) {
  await act(async () => {
    rerender({ ...props, saveStatus: 'saved' })
  })
  await advanceValidationTrigger()
}

/** #1360：校验触发有一拍延迟——等信号非等时长（时序纪律，codex P1）：
 * fake timers 直接推进触发窗口（「不触发」类断言的确定性证据也靠它：
 * 推进整个窗口后仍未调用即不触发）；real timers 轮询「validateWorkflowDraft
 * 调用数」这一可观察信号（调用即触发完成），随后冲刷落定 microtask 让
 * 后续断言读到最新 state。 */
async function advanceValidationTrigger(callCount = 1) {
  if (vi.isFakeTimers()) {
    await act(async () => {
      vi.advanceTimersByTime(VALIDATION_TRIGGER_DELAY_MS)
    })
    return
  }
  await waitFor(
    () => expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(callCount),
    { timeout: 4000 }
  )
  await act(async () => {})
}

describe('useWorkflowStudioActions（#804 定案：自动校验）', () => {
  beforeEach(() => {
    useUiStore.setState({ toast: null })
    vi.clearAllMocks()
    mocks.publishWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
    mocks.validateWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
  })

  it('草稿保存成功后自动静默校验：结果写 validation state，不弹 toast', async () => {
    const { result, rerender } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })
    expect(mocks.validateWorkflowDraft).not.toHaveBeenCalled()

    await flushSaved(rerender, {
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })

    expect(mocks.validateWorkflowDraft).toHaveBeenCalledWith(
      'ws1',
      'key: demo\n'
    )
    expect(result.current.validationMessage).toBe('校验通过')
    expect(result.current.publishing).toBe(false)
    // 静默：不弹 toast（手动校验时代有 toast）。
    expect(useUiStore.getState().toast).toBeNull()
  })

  it('校验失败：写 errors + 校验失败 message（chip 变红、发布禁用的数据源）', async () => {
    mocks.validateWorkflowDraft.mockResolvedValue({
      valid: false,
      errors: ['missing key'],
    })
    const { result, rerender } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })

    await flushSaved(rerender, {
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })

    expect(result.current.validationMessage).toBe('校验失败')
    expect(result.current.validationErrors).toEqual(['missing key'])
    expect(useUiStore.getState().toast).toBeNull()
  })

  it('传输失败自动退避重试，恢复后校验通过（codex 轮 4 P1-1）', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      mocks.validateWorkflowDraft
        .mockRejectedValueOnce(new Error('network error'))
        .mockRejectedValueOnce(new Error('network error'))
        .mockResolvedValue({ valid: true, errors: [] })
      const { result, rerender } = renderActionsHook({
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })

      await flushSaved(rerender, {
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })
      // 第一次传输失败：不写终态 message（不写「校验失败：…」让 effect
      // 永远闭锁——旧实现此处已是终态，revert 即红），等退避重试。
      expect(result.current.validationMessage).toBe('')
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)

      await act(async () => {
        vi.advanceTimersByTime(2100)
      })
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(2)
      expect(result.current.validationMessage).toBe('')

      await act(async () => {
        vi.advanceTimersByTime(4100)
      })
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(3)
      expect(result.current.validationMessage).toBe('校验通过')
    } finally {
      vi.useRealTimers()
    }
  })

  it('传输失败重试耗尽才写终态 校验失败：…（结构失败不重试）', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      mocks.validateWorkflowDraft.mockRejectedValue(new Error('network error'))
      const { result, rerender } = renderActionsHook({
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })

      await flushSaved(rerender, {
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })
      expect(result.current.validationMessage).toBe('')

      // 3 次退避重试（2s/4s/8s）全部失败 → 终态。
      await act(async () => {
        vi.advanceTimersByTime(2100)
      })
      await act(async () => {
        vi.advanceTimersByTime(4100)
      })
      await act(async () => {
        vi.advanceTimersByTime(8100)
      })
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(4)
      expect(result.current.validationMessage).toBe('校验失败：network error')
      expect(result.current.publishing).toBe(false)
      // 终态后不再自动重试（编辑→落盘会重新触发，见它例）。
      await act(async () => {
        vi.advanceTimersByTime(30000)
      })
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(4)
    } finally {
      vi.useRealTimers()
    }
  })

  it('干净态保存成功不触发校验（canSubmit=false：无未发布变更）', async () => {
    // fake timers：推进整个触发窗口后仍未调用，是确定性的「不触发」证据
    //（real timers 下「不触发」没有可等待的信号，只能靠固定休眠——时序纪律禁止）。
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const { rerender } = renderActionsHook({
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
        canSubmit: false,
      })

      await flushSaved(rerender, {
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
        canSubmit: false,
      })

      expect(mocks.validateWorkflowDraft).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('saved 常驻期间不重复触发（只在进入 saved 的边沿跑）', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const { rerender } = renderActionsHook({
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })
      await flushSaved(rerender, {
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)

      // saved → saved（无变化的重复渲染/刷新）：不触发——推进一个完整触发
      // 窗口兜底，若误 arm 必在此时兑现。
      await act(async () => {
        rerender({ saveStatus: 'saved', definitionYaml: 'key: demo\n' })
      })
      await act(async () => {
        vi.advanceTimersByTime(VALIDATION_TRIGGER_DELAY_MS)
      })
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('#1360：内容变化与 settled 同帧到达（chat 采用 agent 落盘草稿）时校验延迟一拍——「未发布变更」窗口可渲染', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const { result, rerender } = renderActionsHook({
        saveStatus: 'saved',
        definitionYaml: 'key: demo\n',
      })
      // 初始内容已落盘：越过触发延迟后校验通过。
      await advanceValidationTrigger()
      expect(result.current.validationMessage).toBe('校验通过')
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)

      // agent turn-end 落盘回同步：画布采用新草稿，保存态同帧保持 settled
      // （hydrate 形态）。旧实现同一提交批次内即发起校验，「未发布变更」
      // （message='' 且未在途）没有可渲染窗口，chip 停在「校验通过」。
      await act(async () => {
        rerender({
          saveStatus: 'saved',
          definitionYaml: 'key: demo\nlabel: agent\n',
        })
      })
      expect(result.current.validationMessage).toBe('')
      expect(result.current.validating).toBe(false)
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)

      // 一拍后才发起校验并落定。
      await advanceValidationTrigger(2)
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledWith(
        'ws1',
        'key: demo\nlabel: agent\n'
      )
      expect(result.current.validationMessage).toBe('校验通过')
    } finally {
      vi.useRealTimers()
    }
  })

  it('草稿再编辑后旧校验结果作废（回「未发布变更」的数据源）', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const { result, rerender } = renderActionsHook({
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })
      await flushSaved(rerender, {
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })
      expect(result.current.validationMessage).toBe('校验通过')

      // 再编辑 → 进入 debounce 窗口（pending）：旧结果作废，且不触发新校验
      // （窗口内按未校验处理，codex 轮 3 P2）——推进一个完整触发窗口兜底。
      await act(async () => {
        rerender({
          saveStatus: 'pending',
          definitionYaml: 'key: demo\nlabel: changed\n',
        })
      })
      expect(result.current.validationMessage).toBe('')
      expect(result.current.validationErrors).toEqual([])
      await act(async () => {
        vi.advanceTimersByTime(VALIDATION_TRIGGER_DELAY_MS)
      })
      expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('hydrate 恢复的服务端草稿（idle + savedAt 非空，无 saved 边沿）也自动校验（codex 轮 3 P2）', async () => {
    renderHook(() =>
      useWorkflowStudioActions(
        'ws1',
        {
          ...draft,
          draftSave: { status: 'idle', savedAt: '2026-08-27T09:05:00+00:00' },
        },
        reload,
        compare
      )
    )

    // 等信号非等时长（#1360 触发延迟后窗口更宽，超时预算按 CI 负载放宽）。
    await waitFor(
      () =>
        expect(mocks.validateWorkflowDraft).toHaveBeenCalledWith(
          'ws1',
          'key: demo\n'
        ),
      { timeout: 4000 }
    )
  })

  it('传输失败终态的显式重试：retryValidation 清空结果 → 自动校验重跑（轮 6 H3）', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      mocks.validateWorkflowDraft.mockRejectedValue(new Error('network error'))
      const { result, rerender } = renderActionsHook({
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })
      await flushSaved(rerender, {
        saveStatus: 'idle',
        definitionYaml: 'key: demo\n',
      })
      // 耗尽 3 次退避 → 终态。
      for (const ms of [2100, 4100, 8100]) {
        await act(async () => {
          vi.advanceTimersByTime(ms)
        })
      }
      expect(result.current.validationMessage).toBe('校验失败：network error')
      const calls = mocks.validateWorkflowDraft.mock.calls.length

      // 网络恢复后用户点「重试校验」（抽屉内按钮）：清空即重跑。
      mocks.validateWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
      await act(async () => {
        result.current.retryValidation()
      })
      // #1360：重跑同样经触发延迟。
      await act(async () => {
        vi.advanceTimersByTime(VALIDATION_TRIGGER_DELAY_MS)
      })
      expect(mocks.validateWorkflowDraft.mock.calls.length).toBeGreaterThan(
        calls
      )
      expect(result.current.validationMessage).toBe('校验通过')
    } finally {
      vi.useRealTimers()
    }
  })

  it('校验结果绑定 workspace+YAML：切到 YAML 相同的 workspace B → 结果作废并对 B 重校验（轮 7 P2）', async () => {
    const { result, rerender } = renderHook(
      ({ ws, saveStatus, definitionYaml }: AutoProps & { ws: string }) =>
        useWorkflowStudioActions(
          ws,
          {
            ...draft,
            definitionYaml,
            // B 侧同样已落盘（hydrate 形态：idle + savedAt）。
            draftSave: {
              status: saveStatus,
              savedAt: '2026-08-27T09:05:00+00:00',
            },
          },
          reload,
          compareWithChanges
        ),
      {
        initialProps: {
          ws: 'ws1',
          saveStatus: 'saved',
          definitionYaml: 'key: demo\n',
        },
      }
    )
    // A 校验通过。
    await waitFor(
      () => expect(result.current.validationMessage).toBe('校验通过'),
      {
        timeout: 4000,
      }
    )
    expect(result.current.canPublish).toBe(true)

    // 切到 YAML 相同的 B：A 的结果必须作废（旧实现保留 → 即红），
    // 并对 B 重新校验。B 的校验挂起（可控 promise）以观察中间态——
    // 否则同一 act 内重校验即落定，观察不到作废窗口。
    let resolveB!: (v: { valid: boolean; errors: string[] }) => void
    mocks.validateWorkflowDraft.mockImplementation((ws: string) =>
      ws === 'ws2'
        ? new Promise((r) => {
            resolveB = r
          })
        : Promise.resolve({ valid: true, errors: [] })
    )
    await act(async () => {
      rerender({
        ws: 'ws2',
        saveStatus: 'saved',
        definitionYaml: 'key: demo\n',
      })
    })
    expect(result.current.validationMessage).toBe('')
    expect(result.current.canPublish).toBe(false)
    await waitFor(
      () =>
        expect(mocks.validateWorkflowDraft).toHaveBeenCalledWith(
          'ws2',
          'key: demo\n'
        ),
      { timeout: 4000 }
    )
    await act(async () => {
      resolveB({ valid: true, errors: [] })
    })
    expect(result.current.validationMessage).toBe('校验通过')
    expect(result.current.canPublish).toBe(true)
  })

  it('校验在途期间草稿再编辑：迟到的结果丢弃，不覆盖新编辑', async () => {
    let resolveValidation!: (value: {
      valid: boolean
      errors: string[]
    }) => void
    mocks.validateWorkflowDraft.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveValidation = resolve
        })
    )
    const { result, rerender } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })
    // 保存成功 → 校验在途。
    await act(async () => {
      rerender({ saveStatus: 'saved', definitionYaml: 'key: demo\n' })
    })
    await advanceValidationTrigger()
    expect(mocks.validateWorkflowDraft).toHaveBeenCalledTimes(1)
    expect(result.current.validating).toBe(true)

    // 校验未回，草稿已改（旧结果即失效）。
    await act(async () => {
      rerender({
        saveStatus: 'pending',
        definitionYaml: 'key: demo\nlabel: newer\n',
      })
    })
    expect(result.current.validationMessage).toBe('')

    // 迟到结果抵达：不得写入。
    await act(async () => {
      resolveValidation!({ valid: true, errors: [] })
    })
    expect(result.current.validationMessage).toBe('')
    expect(result.current.publishing).toBe(false)
  })

  it('发布与校验的在途状态互不覆盖（codex 轮 5 P1：交错序列）', async () => {
    let resolvePublish!: (v: { valid: boolean; errors: string[] }) => void
    mocks.publishWorkflowDraft.mockImplementation(
      () =>
        new Promise((r) => {
          resolvePublish = r
        })
    )
    let resolveValidate!: (v: { valid: boolean; errors: string[] }) => void
    mocks.validateWorkflowDraft.mockImplementation(
      () =>
        new Promise((r) => {
          resolveValidate = r
        })
    )
    const { result, rerender } = renderActionsHook({
      saveStatus: 'idle',
      definitionYaml: 'key: demo\n',
    })
    // 落盘 → 自动校验在途。
    await act(async () => {
      rerender({ saveStatus: 'saved', definitionYaml: 'key: demo\n' })
    })
    await advanceValidationTrigger()
    expect(result.current.validating).toBe(true)
    // 校验在途时发起发布（发布在途）。
    await act(async () => {
      void result.current.publishDraft()
    })
    expect(result.current.publishing).toBe(true)
    // 校验先结束：发布在途标记不得被清（共享 actionState 的旧实现这里
    // 会被盖回 idle → 发布入口提前解禁）。
    await act(async () => {
      resolveValidate({ valid: true, errors: [] })
    })
    expect(result.current.validating).toBe(false)
    expect(result.current.publishing).toBe(true)
    // 发布后结束：校验态已复位，不受发布影响。
    await act(async () => {
      resolvePublish({ valid: true, errors: [] })
    })
    expect(result.current.publishing).toBe(false)
    expect(result.current.validating).toBe(false)
  })
})
