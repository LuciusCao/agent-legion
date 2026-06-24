import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MetadataPanel } from './MetadataPanel'
import { useArtifactStore } from '../stores/artifactStore'

describe('MetadataPanel', () => {
  it('renders empty state when metadata is null', () => {
    useArtifactStore.setState({
      artifacts: { ...useArtifactStore.getState().artifacts, metadata: null },
    })
    render(<MetadataPanel />)
    expect(screen.getByText('暂无元数据')).toBeInTheDocument()
  })

  it('renders metadata as formatted json', () => {
    useArtifactStore.setState({
      artifacts: {
        ...useArtifactStore.getState().artifacts,
        metadata: { title: 'Test', duration: 120 },
      },
    })
    render(<MetadataPanel />)
    expect(screen.getByText(/"title"/)).toBeInTheDocument()
    expect(screen.getByText(/"Test"/)).toBeInTheDocument()
  })
})
