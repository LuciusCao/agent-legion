import { describe, expect, it } from 'vitest'
import {
  PREVIEW_STATE_ACTIVE_POLL_MS,
  PREVIEW_STATE_IDLE_POLL_MS,
  previewPanelStatePollInterval,
} from './usePreviewPanel'

describe('previewPanelStatePollInterval (#965)', () => {
  it('未启用（非 admin）不轮询', () => {
    expect(previewPanelStatePollInterval(false, false)).toBe(false)
    expect(previewPanelStatePollInterval(false, true)).toBe(false)
  })

  it('定制对话开着保持 3s（改一版看一版）', () => {
    expect(previewPanelStatePollInterval(true, true)).toBe(
      PREVIEW_STATE_ACTIVE_POLL_MS
    )
    expect(PREVIEW_STATE_ACTIVE_POLL_MS).toBe(3_000)
  })

  it('定制对话关着降到 30s', () => {
    expect(previewPanelStatePollInterval(true, false)).toBe(
      PREVIEW_STATE_IDLE_POLL_MS
    )
    expect(PREVIEW_STATE_IDLE_POLL_MS).toBe(30_000)
  })
})
