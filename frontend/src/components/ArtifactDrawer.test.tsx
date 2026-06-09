import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ArtifactDrawer } from './ArtifactDrawer'

describe('ArtifactDrawer', () => {
  const artifacts = [
    { name: 'metadata.json', size: 120, content: '{"key":"value"}' },
    { name: 'report.md', content: '# Report' },
  ]
  const onClose = vi.fn()
  const onDownload = vi.fn()

  beforeEach(() => {
    onClose.mockReset()
    onDownload.mockReset()
  })

  it('does not render when closed', () => {
    render(
      <ArtifactDrawer
        open={false}
        artifacts={artifacts}
        onClose={onClose}
        onDownload={onDownload}
      />
    )

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders artifact list when open', () => {
    render(
      <ArtifactDrawer
        open={true}
        artifacts={artifacts}
        onClose={onClose}
        onDownload={onDownload}
      />
    )

    expect(screen.getByText('metadata.json')).toBeInTheDocument()
    expect(screen.getByText('report.md')).toBeInTheDocument()
  })

  it('calls onDownload when download button is clicked', () => {
    render(
      <ArtifactDrawer
        open={true}
        artifacts={artifacts}
        onClose={onClose}
        onDownload={onDownload}
      />
    )

    const downloadButtons = screen.getAllByText('下载')
    fireEvent.click(downloadButtons[0])
    expect(onDownload).toHaveBeenCalledTimes(1)
    expect(onDownload).toHaveBeenCalledWith('metadata.json')
  })

  it('shows formatted JSON preview when artifact is clicked', () => {
    render(
      <ArtifactDrawer
        open={true}
        artifacts={artifacts}
        onClose={onClose}
        onDownload={onDownload}
      />
    )

    fireEvent.click(screen.getByText('metadata.json'))
    expect(screen.getByText(/"key"/)).toBeInTheDocument()
    expect(screen.getByText(/"value"/)).toBeInTheDocument()
  })

  it('shows plain text preview for non-JSON artifacts', () => {
    render(
      <ArtifactDrawer
        open={true}
        artifacts={artifacts}
        onClose={onClose}
        onDownload={onDownload}
      />
    )

    fireEvent.click(screen.getByText('report.md'))
    expect(screen.getByText('# Report')).toBeInTheDocument()
  })

  it('calls onClose when close button is clicked', () => {
    render(
      <ArtifactDrawer
        open={true}
        artifacts={artifacts}
        onClose={onClose}
        onDownload={onDownload}
      />
    )

    fireEvent.click(screen.getByText('关闭'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('renders empty list without errors', () => {
    render(
      <ArtifactDrawer
        open={true}
        artifacts={[]}
        onClose={onClose}
        onDownload={onDownload}
      />
    )

    expect(screen.queryByText('metadata.json')).not.toBeInTheDocument()
  })
})
