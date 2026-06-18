import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ArtifactListDialog } from './ArtifactListDialog'

describe('ArtifactListDialog', () => {
  const onClose = vi.fn()
  const onSelect = vi.fn()

  beforeEach(() => {
    onClose.mockReset()
    onSelect.mockReset()
  })

  it('renders artifact names as buttons', () => {
    render(
      <ArtifactListDialog
        open={true}
        artifacts={['metadata.json', 'report.md']}
        onClose={onClose}
        onSelect={onSelect}
      />
    )

    expect(screen.getByText('metadata.json')).toBeInTheDocument()
    expect(screen.getByText('report.md')).toBeInTheDocument()
  })

  it('calls onSelect with the correct name when an artifact button is clicked', () => {
    render(
      <ArtifactListDialog
        open={true}
        artifacts={['metadata.json', 'report.md']}
        onClose={onClose}
        onSelect={onSelect}
      />
    )

    fireEvent.click(screen.getByText('metadata.json'))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('metadata.json')
  })

  it('shows empty message when artifacts array is empty', () => {
    render(
      <ArtifactListDialog
        open={true}
        artifacts={[]}
        onClose={onClose}
        onSelect={onSelect}
      />
    )

    expect(screen.getByText('暂无产物文件')).toBeInTheDocument()
  })

  it('calls onClose when close button is clicked', () => {
    render(
      <ArtifactListDialog
        open={true}
        artifacts={['metadata.json']}
        onClose={onClose}
        onSelect={onSelect}
      />
    )

    fireEvent.click(screen.getByText('关闭'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when the md-dialog open attribute is removed externally', async () => {
    render(
      <ArtifactListDialog
        open={true}
        artifacts={['metadata.json']}
        onClose={onClose}
        onSelect={onSelect}
      />
    )

    const dialog = document.querySelector('md-dialog')
    expect(dialog).toBeTruthy()
    dialog!.removeAttribute('open')

    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
