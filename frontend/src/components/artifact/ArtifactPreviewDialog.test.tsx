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

  it('renders content in a pre block', () => {
    render(
      <ArtifactPreviewDialog
        open={true}
        name="report.md"
        content="# Report"
        onClose={onClose}
      />
    )

    const pre = document.querySelector('pre')
    expect(pre).toBeInTheDocument()
    expect(pre).toHaveTextContent('# Report')
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
