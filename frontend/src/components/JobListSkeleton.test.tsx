import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { JobListSkeleton } from './JobListSkeleton'

describe('JobListSkeleton', () => {
  it('renders 10 skeleton rows', () => {
    render(<JobListSkeleton />)
    expect(screen.getAllByTestId('skeleton-row')).toHaveLength(10)
  })

  it('marks the list as busy', () => {
    render(<JobListSkeleton />)
    expect(screen.getByTestId('job-list-skeleton')).toHaveAttribute(
      'aria-busy',
      'true'
    )
  })
})
