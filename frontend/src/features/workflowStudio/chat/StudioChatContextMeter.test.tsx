import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StudioChatContextMeter } from './StudioChatContextMeter'

describe('StudioChatContextMeter', () => {
  it('shows used/size and the occupancy percentage from usage_update (#694)', () => {
    render(
      <StudioChatContextMeter
        usage={{ used: 12_345, size: 262_144 }}
        compacting={false}
      />
    )
    expect(screen.getByLabelText('上下文用量')).toHaveTextContent(
      '上下文 12.3k / 262.1k tokens（5%）'
    )
  })

  it('renders nothing before the agent reports usage', () => {
    const { container } = render(
      <StudioChatContextMeter usage={null} compacting={false} />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the compacting hint even without usage yet', () => {
    render(<StudioChatContextMeter usage={null} compacting />)
    expect(screen.getByLabelText('上下文用量')).toHaveTextContent(
      '正在压缩上下文'
    )
  })
})
