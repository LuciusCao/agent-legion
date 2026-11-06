import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkflowDraftConflictError } from '../../../api/workflowDraft'
import { DraftSaveController } from './draftSaveController'
import type { PutWorkflowDraftFn } from './draftSaveTypes'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

const saved = (generation: number) => ({
  definition_yaml: null,
  updated_at: '2026-11-05T00:00:00+00:00',
  definition_hash: `hash-${generation}`,
  generation,
})

describe('DraftSaveController request ordering', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('serializes a queued save and issues it with the preceding save generation', async () => {
    const first = deferred<Awaited<ReturnType<PutWorkflowDraftFn>>>()
    const put = vi
      .fn<PutWorkflowDraftFn>()
      .mockImplementationOnce(() => first.promise)
      .mockResolvedValue(saved(3))
    const controller = new DraftSaveController(put)
    controller.hydrate('base', null, 1)

    controller.schedule('edit A')
    await vi.advanceTimersByTimeAsync(800)
    controller.schedule('edit B')
    await vi.advanceTimersByTimeAsync(800)
    expect(put).toHaveBeenCalledTimes(1)

    first.resolve(saved(2))
    await first.promise

    expect(put).toHaveBeenCalledTimes(2)
    expect(put.mock.calls[1]).toEqual(['edit B', false, 2])
  })

  it('fails a conflict flush and keeps later automatic flushes suspended', async () => {
    const put = vi.fn<PutWorkflowDraftFn>().mockRejectedValue(
      new WorkflowDraftConflictError({
        message: 'Workflow draft conflict',
        current_draft: {
          definition_yaml: 'agent edit',
          updated_at: '2026-11-05T00:01:00+00:00',
          definition_hash: 'agent-hash',
          generation: 9,
        },
      })
    )
    const controller = new DraftSaveController(put)
    controller.hydrate('base', null, 1)
    controller.schedule('local edit')

    const flushed = await controller.flushNow()

    expect(flushed.ok).toBe(false)
    expect(flushed.state.conflict).toBe(true)
    expect(flushed.state.savedYaml).toBe('base')
    expect(controller.hasUnsaved()).toBe(true)
    expect((await controller.flushNow()).ok).toBe(false)
    expect(put).toHaveBeenCalledTimes(1)
  })
})
