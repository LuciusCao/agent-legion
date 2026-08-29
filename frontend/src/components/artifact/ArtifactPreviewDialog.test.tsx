import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ArtifactPreviewDialog } from './ArtifactPreviewDialog'

describe('ArtifactPreviewDialog', () => {
  const onClose = vi.fn()

  beforeEach(() => {
    onClose.mockReset()
  })

  it('renders the artifact name as headline', () => {
    render(
      <ArtifactPreviewDialog
        open={true}
        name="metadata.json"
        content='{"key":"value"}'
        onClose={onClose}
      />
    )

    expect(screen.getByText('metadata.json')).toBeInTheDocument()
  })

  it('renders plain text content in a pre block', () => {
    render(
      <ArtifactPreviewDialog
        open={true}
        name="report.txt"
        content="# Report"
        onClose={onClose}
      />
    )

    const pre = document.querySelector('pre')
    expect(pre).toBeInTheDocument()
    expect(pre).toHaveTextContent('# Report')
  })

  it('renders markdown artifacts as sanitized HTML with a source toggle', () => {
    render(
      <ArtifactPreviewDialog
        open={true}
        name="report.md"
        content="# Report"
        onClose={onClose}
      />
    )

    // Rendered by default: the heading text appears as an h1, not a pre.
    expect(screen.getByRole('heading', { name: 'Report' })).toBeInTheDocument()
    expect(document.querySelector('pre')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '源码' }))
    const pre = document.querySelector('pre')
    expect(pre).toBeInTheDocument()
    expect(pre).toHaveTextContent('# Report')
  })

  it('renders html artifacts in a sandboxed iframe', () => {
    render(
      <ArtifactPreviewDialog
        open={true}
        name="lesson.html"
        content="<h1>课件</h1>"
        onClose={onClose}
      />
    )

    const frame = document.querySelector('iframe')
    expect(frame).toBeInTheDocument()
    expect(frame).toHaveAttribute('sandbox', '')
    expect(frame).toHaveAttribute('srcdoc', '<h1>课件</h1>')
  })

  it('renders JSON content as an interactive tree', () => {
    render(
      <ArtifactPreviewDialog
        open={true}
        name="metadata.json"
        content='{"key":"value"}'
        onClose={onClose}
      />
    )

    expect(screen.getByText('key')).toBeInTheDocument()
    expect(screen.getByText('"value"')).toBeInTheDocument()
    expect(document.querySelector('pre')).not.toBeInTheDocument()
  })

  it('falls back to pre block for invalid JSON', () => {
    render(
      <ArtifactPreviewDialog
        open={true}
        name="metadata.json"
        content="{not valid json}"
        onClose={onClose}
      />
    )

    const pre = document.querySelector('pre')
    expect(pre).toBeInTheDocument()
    expect(pre).toHaveTextContent('{not valid json}')
  })

  it('calls onClose when close button is clicked', () => {
    render(
      <ArtifactPreviewDialog
        open={true}
        name="metadata.json"
        content='{"key":"value"}'
        onClose={onClose}
      />
    )

    fireEvent.click(screen.getByText('关闭'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
