import { beforeEach, describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AddDialog } from './AddDialog'
import { api } from '../api'
import { useUiStore } from '../stores/uiStore'

vi.mock('../api', () => ({
  api: vi.fn(),
  fetchWorkflowDefinition: vi.fn(),
}))

const mockApi = vi.mocked(api)

function enterResourceIds(value: string) {
  const input = document.querySelector(
    'md-outlined-text-field[type="textarea"]'
  ) as HTMLInputElement
  input.value = value
  fireEvent.input(input)
}

describe('AddDialog', () => {
  beforeEach(() => {
    mockApi.mockReset()
    useUiStore.setState({ addContentType: 'knowledge' })
  })

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

    enterResourceIds('x11090605')

    expect(button).not.toHaveAttribute('disabled')
  })

  it('switches the video content type and updates the input label', () => {
    render(<AddDialog open={true} onClose={vi.fn()} context="video" />)

    fireEvent.click(screen.getByText('题目'))

    expect(useUiStore.getState().addContentType).toBe('question')
    expect(
      document.querySelector('md-outlined-text-field[label="题目 ID"]')
    ).toBeTruthy()
  })

  it('submits normalized video resource inputs', async () => {
    mockApi.mockResolvedValue({ videos: [], results: [] })
    render(<AddDialog open={true} onClose={vi.fn()} context="video" />)
    enterResourceIds('x11090605, uuid-1\nx11090606')

    fireEvent.click(screen.getByText('加入队列'))

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/api/videos', {
        method: 'POST',
        body: JSON.stringify({
          items: [
            {
              content_type: 'knowledge',
              external_id: 'x11090605',
              source_uuid: 'uuid-1',
            },
            {
              content_type: 'knowledge',
              external_id: 'x11090606',
              source_uuid: '',
            },
          ],
        }),
      })
    })
  })
})
