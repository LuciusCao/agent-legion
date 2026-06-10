import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AddDialog } from './AddDialog'

describe('AddDialog', () => {
  it('renders dialog with correct title', () => {
    render(<AddDialog open={true} onClose={vi.fn()} />)
    expect(screen.getByText('添加资源')).toBeInTheDocument()
  })
})
