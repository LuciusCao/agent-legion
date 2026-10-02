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

  it('offers a source toggle for JSON that shows the raw bytes (#777)', () => {
    const raw = '{\n  "key": "value",\n  "n": 1\n}'
    render(
      <ArtifactPreviewDialog
        open={true}
        name="understanding.json"
        content={raw}
        onClose={onClose}
      />
    )

    // Tree view stays the default.
    expect(screen.getByRole('button', { name: '预览' })).toBeInTheDocument()
    expect(screen.getByText('"value"')).toBeInTheDocument()
    expect(document.querySelector('pre')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '源码' }))
    const pre = document.querySelector('pre')
    expect(pre).toBeInTheDocument()
    // Raw text verbatim: commas and quoted keys intact.
    expect(pre?.textContent).toBe(raw)
    expect(screen.queryByText('全部展开')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '预览' }))
    expect(document.querySelector('pre')).not.toBeInTheDocument()
    expect(screen.getByText('"value"')).toBeInTheDocument()
  })

  it('resets to the tree view when switching to another artifact', () => {
    const { rerender } = render(
      <ArtifactPreviewDialog
        open={true}
        name="a.json"
        content='{"key":"value"}'
        onClose={onClose}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: '源码' }))
    expect(document.querySelector('pre')).toBeInTheDocument()

    rerender(
      <ArtifactPreviewDialog
        open={true}
        name="b.json"
        content='{"other":"x"}'
        onClose={onClose}
      />
    )
    expect(document.querySelector('pre')).not.toBeInTheDocument()
    expect(screen.getByText('other')).toBeInTheDocument()
  })

  it('resets to the rendered view after close and reopen', () => {
    const view = (open: boolean, name: string, content: string) => (
      <ArtifactPreviewDialog
        open={open}
        name={name}
        content={content}
        onClose={onClose}
      />
    )
    const { rerender } = render(view(true, 'a.json', '{"key":"value"}'))
    fireEvent.click(screen.getByRole('button', { name: '源码' }))
    expect(document.querySelector('pre')).toBeInTheDocument()

    // Same artifact reopened: back to the tree.
    rerender(view(false, 'a.json', '{"key":"value"}'))
    rerender(view(true, 'a.json', '{"key":"value"}'))
    expect(document.querySelector('pre')).not.toBeInTheDocument()
    expect(screen.getByText('"value"')).toBeInTheDocument()

    // Pre-existing markdown toggle gets the same reset.
    rerender(view(true, 'report.md', '# Report'))
    fireEvent.click(screen.getByRole('button', { name: '源码' }))
    expect(document.querySelector('pre')).toBeInTheDocument()
    rerender(view(false, 'report.md', '# Report'))
    rerender(view(true, 'report.md', '# Report'))
    expect(document.querySelector('pre')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Report' })).toBeInTheDocument()
  })

  it('does not show the toggle for plain text artifacts', () => {
    render(
      <ArtifactPreviewDialog
        open={true}
        name="report.txt"
        content="hello"
        onClose={onClose}
      />
    )

    expect(
      screen.queryByRole('button', { name: '源码' })
    ).not.toBeInTheDocument()
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
