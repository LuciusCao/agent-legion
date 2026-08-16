import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { NonUploadableNotice } from './NonUploadableNotice'

vi.mock('../../api', () => ({
  fetchJobArtifact: vi.fn(),
}))

import { fetchJobArtifact } from '../../api'

const fetchMock = vi.mocked(fetchJobArtifact)

function manifest(payload: object) {
  return { name: 'manifest.json', content: JSON.stringify(payload) }
}

describe('NonUploadableNotice', () => {
  beforeEach(() => {
    fetchMock.mockReset()
  })

  it('shows the skip reason when the manifest marks the job not uploadable', async () => {
    fetchMock.mockResolvedValue(
      manifest({
        uploadable: false,
        skip_reason: '该题无文字题干（互动/模糊题）',
      })
    )
    render(
      <NonUploadableNotice
        jobId="j1"
        jobStatus="completed"
        artifacts={['manifest.json']}
      />
    )
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        '不适用 · 不可上传：该题无文字题干（互动/模糊题）'
      )
    )
  })

  it('stays hidden when the manifest says uploadable', async () => {
    fetchMock.mockResolvedValue(manifest({ uploadable: true }))
    const { container } = render(
      <NonUploadableNotice
        jobId="j1"
        jobStatus="completed"
        artifacts={['manifest.json']}
      />
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(container).toBeEmptyDOMElement()
  })

  it('does not fetch for non-completed jobs', () => {
    const { container } = render(
      <NonUploadableNotice
        jobId="j1"
        jobStatus="failed"
        artifacts={['manifest.json']}
      />
    )
    expect(fetchMock).not.toHaveBeenCalled()
    expect(container).toBeEmptyDOMElement()
  })

  it('does not fetch when the job wrote no manifest', () => {
    const { container } = render(
      <NonUploadableNotice jobId="j1" jobStatus="completed" artifacts={[]} />
    )
    expect(fetchMock).not.toHaveBeenCalled()
    expect(container).toBeEmptyDOMElement()
  })

  it('stays hidden when the manifest fetch fails', async () => {
    fetchMock.mockRejectedValue(new Error('not found'))
    const { container } = render(
      <NonUploadableNotice
        jobId="j1"
        jobStatus="completed"
        artifacts={['manifest.json']}
      />
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(container).toBeEmptyDOMElement()
  })
})
