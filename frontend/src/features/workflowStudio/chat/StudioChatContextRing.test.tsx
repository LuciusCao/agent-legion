import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
  contextUsageFromSession,
  StudioChatContextRing,
} from './StudioChatContextRing'

async function tooltipText(): Promise<string> {
  const tip = await screen.findByRole('tooltip')
  return tip.textContent ?? ''
}

describe('StudioChatContextRing', () => {
  it('renders nothing without usage data and no compaction', () => {
    const { container } = render(
      <StudioChatContextRing used={null} size={null} compacting={false} />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows exact token counts and the percentage on hover only', async () => {
    render(
      <StudioChatContextRing used={12_345} size={262_144} compacting={false} />
    )
    const ring = screen.getByLabelText('上下文用量')
    // 环旁不常驻百分比数字，精确值只在 hover tooltip 里。
    expect(ring).toHaveTextContent('')
    fireEvent.mouseOver(ring)
    expect(await tooltipText()).toBe('上下文 12.3k / 262.1k tokens（5%）')
  })

  it('uses the M unit for context windows at or above 1M', async () => {
    render(
      <StudioChatContextRing
        used={36_700}
        size={1_048_576}
        compacting={false}
      />
    )
    fireEvent.mouseOver(screen.getByLabelText('上下文用量'))
    expect(await tooltipText()).toBe('上下文 36.7k / 1.0M tokens（3%）')
  })

  it('renders while compacting even without usage yet (#694)', async () => {
    render(<StudioChatContextRing used={null} size={null} compacting />)
    const ring = screen.getByLabelText('上下文用量')
    fireEvent.mouseOver(ring)
    expect(await tooltipText()).toBe('正在压缩上下文…')
  })

  it('appends the compacting hint to the usage tooltip', async () => {
    render(<StudioChatContextRing used={1000} size={2000} compacting />)
    fireEvent.mouseOver(screen.getByLabelText('上下文用量'))
    expect(await tooltipText()).toBe(
      '上下文 1.0k / 2.0k tokens（50%） · 正在压缩上下文…'
    )
  })

  it('clamps the percentage at 100 when usage exceeds the window', async () => {
    render(
      <StudioChatContextRing used={300_000} size={262_144} compacting={false} />
    )
    fireEvent.mouseOver(screen.getByLabelText('上下文用量'))
    expect(await tooltipText()).toContain('（100%）')
  })
})

describe('contextUsageFromSession', () => {
  it('returns null when the session carries no usage', () => {
    expect(contextUsageFromSession({ usage: null })).toBeNull()
    expect(contextUsageFromSession({})).toBeNull()
  })

  it('narrows numeric fields and nulls out missing ones', () => {
    expect(
      contextUsageFromSession({ usage: { used: 1000, size: 2000 } })
    ).toEqual({ used: 1000, size: 2000 })
    expect(contextUsageFromSession({ usage: { used: 'x' } })).toEqual({
      used: null,
      size: null,
    })
  })
})
