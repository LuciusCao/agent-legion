import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { useUiStore } from '../stores/uiStore'
import WorkspaceAgents from './WorkspaceAgents'

describe('WorkspaceAgents', () => {
  beforeEach(() => {
    useUiStore.setState({
      agents: [],
    })
  })

  it('renders the global agent pool state', () => {
    useUiStore.setState({
      agents: [
        {
          id: 'agent-1',
          name: 'Agent 1',
          busy: true,
          task_count: 1,
          max_tasks: 2,
          current_video_id: 'video-1',
          current_title: 'Video 1',
        },
      ],
    })

    render(<WorkspaceAgents isVideoHive={false} />)

    expect(screen.getByText('全局 Agent 池')).toBeInTheDocument()
    expect(screen.getByText('Agent 1')).toBeInTheDocument()
    expect(screen.getByText(/Video 1/)).toBeInTheDocument()
  })
})
