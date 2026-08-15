import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { VideoContentPanel } from './VideoContentPanel'

describe('VideoContentPanel', () => {
  it('renders the transitional empty state (detail endpoint retired, #11)', () => {
    render(<VideoContentPanel jobId="job1" />)
    expect(screen.getByTestId('video-content-panel')).toBeInTheDocument()
    expect(screen.getByText('视频内容尚未生成')).toBeInTheDocument()
  })
})
