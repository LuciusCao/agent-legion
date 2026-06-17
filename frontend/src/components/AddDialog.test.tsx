import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AddDialog } from './AddDialog'

describe('AddDialog', () => {
  it('renders dialog with correct title', () => {
    render(<AddDialog open={true} onClose={vi.fn()} />)
    expect(screen.getByText('添加资源')).toBeInTheDocument()
  })

  it('disables submit button when input is empty and enables after typing', () => {
    render(<AddDialog open={true} onClose={vi.fn()} context="video" />)

    const button = screen
      .getByText('加入队列')
      .closest('md-filled-button') as HTMLElement
    expect(button).toHaveAttribute('disabled')

    const input = document.querySelector(
      'md-outlined-text-field[type="textarea"]'
    ) as HTMLInputElement
    input.value = 'x11090605'
    fireEvent.input(input)

    expect(button).not.toHaveAttribute('disabled')
  })
})
