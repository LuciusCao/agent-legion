/* #1196 第 1 项：hydrate/adopt 的单帧假 settled。
   保存状态推进（useDraftServerSync → controller.hydrate）与画布写入
   （useServerDraftApply）、schedule effect 分属不同 effect 链，可渲染出
   「settled + savedHash=H_D 但画布 ≠ D」的一帧。本文件按真实组合
   （与 useWorkflowStudioDraftStore 同构）逐帧记录 (画布, 保存状态)，断言
   不变量而非中间态：任何一帧里，草稿卡 hash 短路（savedIdentityConfirms）
   成立 ⇒ 画布内容就是 hash 所描述的草稿 D。 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowDraftStoreResponse } from '../../../api/workflowDraft'
import { DraftSaveController } from './draftSaveController'
import { savedIdentityConfirms, type DraftSaveState } from './draftSaveTypes'
import { useServerDraftApply } from './useServerDraftApply'
import { useWorkflowDraftPersistence } from './useWorkflowDraftPersistence'

const mocks = { putWorkflowDraft: vi.fn() }

vi.mock('../../../api', () => ({
  putWorkflowDraft: (...args: unknown[]) => mocks.putWorkflowDraft(...args),
}))

const ORIGINAL = 'key: demo\nlabel: Base\n'
const SERVER_D = 'key: demo\nlabel: Agent\n'
const H_D = 'hash-agent-d'
const SERVER: WorkflowDraftStoreResponse = {
  definition_yaml: SERVER_D,
  updated_at: '2026-10-10T01:00:00+00:00',
  definition_hash: H_D,
} as WorkflowDraftStoreResponse

type Frame = { canvas: string; save: DraftSaveState }
type Props = { serverDraft: WorkflowDraftStoreResponse | undefined }

function renderComposition(frames: Frame[]) {
  return renderHook<
    {
      setDraftYaml: (value: string) => void
      persistence: ReturnType<typeof useWorkflowDraftPersistence>
      draftYaml: string
    },
    Props
  >(
    ({ serverDraft }) => {
      const [draftYaml, setDraftYamlState] = useState(ORIGINAL)
      const { setDraftYaml, consumeConflict } = useServerDraftApply(
        'ws1',
        ORIGINAL,
        serverDraft?.definition_yaml,
        serverDraft?.updated_at,
        setDraftYamlState,
        draftYaml,
        serverDraft === undefined ? undefined : serverDraft.definition_hash
      )
      const persistence = useWorkflowDraftPersistence(
        'ws1',
        draftYaml,
        ORIGINAL,
        serverDraft,
        false,
        consumeConflict
      )
      frames.push({ canvas: draftYaml, save: persistence.state })
      return { setDraftYaml, persistence, draftYaml }
    },
    { initialProps: { serverDraft: undefined } }
  )
}

/* 不变量：短路成立的每一帧，画布都必须正是 H_D 描述的草稿 D。 */
function expectNoFalseShortCircuit(frames: Frame[]) {
  const violations = frames.filter(
    (frame) =>
      savedIdentityConfirms(frame.save, frame.canvas, H_D) &&
      frame.canvas !== SERVER_D
  )
  expect(violations).toEqual([])
}

describe('草稿卡 hash 短路不变量（#1196 单帧假 settled）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.putWorkflowDraft.mockResolvedValue(SERVER)
  })

  it('pre-hydration 编辑后服务端草稿到达：任何一帧都不误短路', async () => {
    const frames: Frame[] = []
    const { result, rerender } = renderComposition(frames)
    // GET 未回时用户已编辑（画布 = E，touched）。
    act(() => result.current.setDraftYaml('key: demo\nlabel: Mine\n'))
    // 服务端草稿 D（hash H_D）到达：hydrate 推进 idle+savedHash=H_D，
    // 画布保留用户编辑 E（冲突挂起）。
    rerender({ serverDraft: SERVER })
    await waitFor(() =>
      expect(result.current.persistence.state.conflict).toBe(true)
    )
    expect(result.current.draftYaml).toBe('key: demo\nlabel: Mine\n')
    expectNoFalseShortCircuit(frames)
  })

  it('无本地编辑时服务端草稿到达并写入画布：落定后短路成立', async () => {
    const frames: Frame[] = []
    const { result, rerender } = renderComposition(frames)
    rerender({ serverDraft: SERVER })
    await waitFor(() => expect(result.current.draftYaml).toBe(SERVER_D))
    expectNoFalseShortCircuit(frames)
    // 正向：画布 = D 且已落定后，hash 短路必须成立（不是靠永远提示过关）。
    expect(
      savedIdentityConfirms(
        result.current.persistence.state,
        result.current.draftYaml,
        H_D
      )
    ).toBe(true)
  })

  it('冲突后采用服务端版本（adopt）：任何一帧都不误短路', async () => {
    const frames: Frame[] = []
    const { result, rerender } = renderComposition(frames)
    act(() => result.current.setDraftYaml('key: demo\nlabel: Mine\n'))
    rerender({ serverDraft: SERVER })
    await waitFor(() =>
      expect(result.current.persistence.state.conflict).toBe(true)
    )
    act(() =>
      result.current.persistence.adoptServerDraft(
        SERVER_D,
        SERVER.updated_at ?? null,
        result.current.setDraftYaml,
        H_D
      )
    )
    await waitFor(() => expect(result.current.draftYaml).toBe(SERVER_D))
    expectNoFalseShortCircuit(frames)
  })
})

describe('savedYaml 与 savedHash 成对（#1196）', () => {
  it('每次状态转换 savedYaml 都是已持久化基线；换基线而未给身份时清除旧 hash', () => {
    const controller = new DraftSaveController(vi.fn())
    let last: DraftSaveState | null = null
    controller.subscribe((state) => {
      last = state
    })
    controller.hydrate(SERVER_D, SERVER.updated_at, H_D)
    expect(last).toMatchObject({ savedYaml: SERVER_D, savedHash: H_D })
    controller.schedule('key: demo\nlabel: Mine\n')
    expect(last).toMatchObject({ status: 'pending', savedYaml: SERVER_D })
    // 旧冲突事件不带 hash（undefined）：内容已换，旧 H_D 不再描述它。
    controller.adoptServerDraft('key: demo\nlabel: Other\n', null)
    expect(last).toMatchObject({
      savedYaml: 'key: demo\nlabel: Other\n',
      savedHash: null,
    })
  })
})
