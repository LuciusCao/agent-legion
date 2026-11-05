import { describe, expect, it } from 'vitest'
import type { WorkflowDraftStoreResponse } from '../../../api/workflowDraft'
import {
  ServerDraftSyncTracker,
  type ServerDraftSyncInput,
} from './serverDraftSync'

const ORIGINAL = 'key: demo\nlabel: Base\n'
const BASE_AT = '2026-08-27T01:00:00+00:00'
const NEXT_AT = '2026-08-27T02:00:00+00:00'

function draft(
  overrides: Partial<WorkflowDraftStoreResponse> = {}
): WorkflowDraftStoreResponse {
  return {
    definition_yaml: ORIGINAL,
    updated_at: BASE_AT,
    definition_hash: 'base-hash',
    generation: 7,
    ...overrides,
  }
}

function input(
  overrides: Partial<ServerDraftSyncInput> = {}
): ServerDraftSyncInput {
  return {
    serverDraft: draft(),
    originalYaml: ORIGINAL,
    hydrated: true,
    conflict: null,
    controllerInConflict: () => false,
    ...overrides,
  }
}

describe('ServerDraftSyncTracker', () => {
  it('waits for the query and canonical baseline before hydrating', () => {
    const tracker = new ServerDraftSyncTracker()
    expect(
      tracker.evaluate(input({ serverDraft: undefined, hydrated: false }))
    ).toEqual({ action: 'not-ready' })
    expect(
      tracker.evaluate(input({ originalYaml: '', hydrated: false }))
    ).toEqual({ action: 'not-ready' })
    expect(tracker.isHydrated()).toBe(false)
  })

  it('hydrates once and ignores stale or repeated responses', () => {
    const tracker = new ServerDraftSyncTracker()
    expect(tracker.evaluate(input({ hydrated: false }))).toEqual({
      action: 'hydrate',
      yaml: ORIGINAL,
      updatedAt: BASE_AT,
      generation: 7,
      hash: 'base-hash',
    })
    expect(tracker.isHydrated()).toBe(true)

    // 同代次即使时间戳更晚也是重复响应；更旧代次不得回退 CAS 基线。
    expect(
      tracker.evaluate(input({ serverDraft: draft({ updated_at: NEXT_AT }) }))
    ).toEqual({ action: 'noop' })
    expect(
      tracker.evaluate(
        input({
          serverDraft: draft({ generation: 6, updated_at: NEXT_AT }),
        })
      )
    ).toEqual({ action: 'noop' })
  })

  it('advances hydration by generation even when updated_at is unchanged', () => {
    const tracker = new ServerDraftSyncTracker()
    tracker.evaluate(input({ hydrated: false }))
    expect(
      tracker.evaluate(
        input({
          serverDraft: draft({
            definition_yaml: 'key: demo\nlabel: Agent\n',
            generation: 8,
          }),
        })
      )
    ).toMatchObject({ action: 'hydrate', generation: 8 })
  })

  it('surfaces a conflict and advances the comparison baseline without hydrating', () => {
    const tracker = new ServerDraftSyncTracker()
    tracker.evaluate(input({ hydrated: false }))
    const conflict = {
      yaml: 'key: demo\nlabel: Agent\n',
      updatedAt: NEXT_AT,
      hash: 'agent-hash',
      generation: 9,
    }
    expect(tracker.evaluate(input({ conflict }))).toEqual({
      action: 'conflict',
      conflict,
    })

    // 冲突载荷已推进代次；同一服务端草稿的后续重取不得再水合或清冲突。
    expect(
      tracker.evaluate(
        input({
          serverDraft: draft({
            definition_yaml: conflict.yaml,
            updated_at: NEXT_AT,
            definition_hash: conflict.hash,
            generation: conflict.generation,
          }),
        })
      )
    ).toEqual({ action: 'noop' })
  })

  it('does not let an empty response clear an unresolved conflict', () => {
    const tracker = new ServerDraftSyncTracker()
    tracker.evaluate(input({ hydrated: false }))
    const empty = draft({
      definition_yaml: null,
      updated_at: null,
      definition_hash: null,
      generation: 9,
    })
    expect(
      tracker.evaluate(
        input({ serverDraft: empty, controllerInConflict: () => true })
      )
    ).toEqual({ action: 'noop' })

    // 冲突期间没有写入空态幂等键；显式解除后同一响应可补齐水合。
    expect(tracker.evaluate(input({ serverDraft: empty }))).toEqual({
      action: 'hydrate',
      yaml: ORIGINAL,
      updatedAt: null,
      generation: 9,
      hash: null,
    })
  })

  it('keys empty-state hydration by canonical yaml and generation', () => {
    const tracker = new ServerDraftSyncTracker()
    const empty = draft({
      definition_yaml: null,
      updated_at: null,
      definition_hash: null,
      generation: 0,
    })
    expect(
      tracker.evaluate(input({ serverDraft: empty, hydrated: false })).action
    ).toBe('hydrate')
    expect(tracker.evaluate(input({ serverDraft: empty })).action).toBe('noop')
    expect(
      tracker.evaluate(input({ serverDraft: { ...empty, generation: 1 } }))
        .action
    ).toBe('hydrate')

    tracker.reset()
    expect(tracker.isHydrated()).toBe(false)
    expect(
      tracker.evaluate(
        input({ serverDraft: { ...empty, generation: 1 }, hydrated: false })
      ).action
    ).toBe('hydrate')
  })
})
