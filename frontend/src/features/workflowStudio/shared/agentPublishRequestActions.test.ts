import { beforeEach, describe, expect, it, vi } from 'vitest'
import { confirmAgentPublishRequest } from './agentPublishRequestActions'
import { fetchWorkflowDraft } from '../../../api/workflowDraft'

vi.mock('../../../api/workflowDraft', () => ({
  fetchWorkflowDraft: vi.fn(),
}))

const mockFetchWorkflowDraft = vi.mocked(fetchWorkflowDraft)

describe('confirmAgentPublishRequest', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('awaits flush before re-reading and confirming, then registers the server yaml', async () => {
    const order: string[] = []
    let releaseFlush: (() => void) | undefined
    const flushDraftSave = vi.fn(
      () =>
        new Promise<{ ok: boolean; state: { status: 'saved'; savedAt: null } }>(
          (resolve) => {
            order.push('flush:start')
            releaseFlush = () => {
              order.push('flush:done')
              resolve({ ok: true, state: { status: 'saved', savedAt: null } })
            }
          }
        )
    )
    const markDraftPublished = vi.fn(() => order.push('mark'))
    const confirm = vi.fn(async (onConfirmed?: () => void) => {
      order.push('confirm')
      onConfirmed?.()
    })
    mockFetchWorkflowDraft.mockImplementation(async () => {
      order.push('read')
      return { generation: 1, definition_yaml: 'key: server\n' }
    })

    const pending = confirmAgentPublishRequest({
      workspaceId: 'ws1',
      studio: {
        flushDraftSave,
        definitionYaml: 'key: canvas\n',
        markDraftPublished,
      },
      agentRequest: { confirm },
      showToast: vi.fn(),
    })

    expect(order).toEqual(['flush:start'])
    expect(mockFetchWorkflowDraft).not.toHaveBeenCalled()
    expect(confirm).not.toHaveBeenCalled()
    releaseFlush?.()
    await expect(pending).resolves.toBe(true)
    expect(order).toEqual([
      'flush:start',
      'flush:done',
      'read',
      'confirm',
      'mark',
    ])
    expect(markDraftPublished).toHaveBeenCalledWith('key: server\n')
  })

  it('aborts before re-read and confirm when the live flush result failed', async () => {
    const showToast = vi.fn()
    const confirm = vi.fn()
    const result = await confirmAgentPublishRequest({
      workspaceId: 'ws1',
      studio: {
        flushDraftSave: vi.fn().mockResolvedValue({
          ok: false,
          state: { status: 'error', savedAt: null },
        }),
        definitionYaml: 'key: canvas\n',
        markDraftPublished: vi.fn(),
      },
      agentRequest: { confirm },
      showToast,
    })

    expect(result).toBe(false)
    expect(showToast).toHaveBeenCalledWith(
      '草稿尚未保存成功，请稍后重试确认',
      'error'
    )
    expect(mockFetchWorkflowDraft).not.toHaveBeenCalled()
    expect(confirm).not.toHaveBeenCalled()
  })

  it('keeps the existing confirm error handling outside the draft guard', async () => {
    const failure = new Error('confirm failed')
    const confirm = vi.fn().mockRejectedValue(failure)
    const showToast = vi.fn()
    mockFetchWorkflowDraft.mockResolvedValue({
      generation: 1,
      definition_yaml: null,
    })

    await expect(
      confirmAgentPublishRequest({
        workspaceId: 'ws1',
        studio: {
          flushDraftSave: vi.fn().mockResolvedValue({
            ok: true,
            state: { status: 'saved', savedAt: null },
          }),
          definitionYaml: 'key: canvas\n',
          markDraftPublished: vi.fn(),
        },
        agentRequest: { confirm },
        showToast,
      })
    ).rejects.toBe(failure)
    expect(showToast).not.toHaveBeenCalled()
  })
})
