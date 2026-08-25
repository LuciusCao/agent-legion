import { fireEvent, render, screen, within } from '@testing-library/react'
import { act } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useSettingStore } from '../../stores/settingStore'
import { WorkflowStudioLayout } from './WorkflowStudioLayout'

vi.mock('./chat/StudioChatPanel', () => ({
  StudioChatPanel: (props: Record<string, unknown>) => {
    chatPanelProps(props)
    return <div>chat panel stub</div>
  },
}))

const chatPanelProps = vi.fn()

const workflow = {
  key: 'demo_video_workflow',
  label: '知识视频 DAG',
  intake: { modes: [] },
  nodes: [],
  edges: [],
}

const revision = {
  id: 'rev-active',
  workspace_id: 'ws1',
  workflow_key: 'demo_video_workflow',
  version: 1,
  status: 'active',
  definition_hash: '17d8077e',
  created_at: '2026-07-06T10:00:00Z',
  published_at: '2026-07-06T10:05:00Z',
}

const baseProps = {
  loadState: 'ready' as const,
  actionState: 'idle' as const,
  workflow,
  revision,
  activeRevision: revision,
  revisions: [revision],
  executorCatalog: [],
  agentCatalog: [],
  agentCatalogError: false,
  retryAgentCatalog: vi.fn(),
  definitionYaml: 'key: demo_video_workflow\nlabel: 知识视频 DAG\n',
  setDefinitionYaml: vi.fn(),
  selectedNodeKey: null,
  setSelectedNodeKey: vi.fn(),
  validationErrors: [],
  validationMessage: '',
  compareErrors: null,
  compareSummary: null,
  compareState: 'idle' as const,
  dirty: false,
  canSubmit: false,
  canPublish: false,
  createsRevision: true,
  nodes: [],
  edges: [],
  reviewDialogOpen: false,
  closeReviewDialog: vi.fn(),
  dagFullscreenOpen: false,
  setDagFullscreenOpen: vi.fn(),
  canvasMode: 'dag' as const,
  setCanvasMode: vi.fn(),
  onValidate: vi.fn(),
  onPublish: vi.fn(),
  onReset: vi.fn(),
  onShowChanges: vi.fn(),
  publishDraft: vi.fn(),
  viewMode: 'draft' as const,
  selectedRevisionId: revision.id,
  readOnly: false,
  hasPreservedDraft: false,
  isLoadingRevision: false,
  revisionLoadError: null,
  selectRevision: vi.fn(),
  backToDraft: vi.fn(),
  useViewedRevisionAsDraft: vi.fn(),
}

describe('WorkflowStudioLayout', () => {
  const localStore = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => localStore.get(key) ?? null,
    setItem: (key: string, value: string) => void localStore.set(key, value),
    removeItem: (key: string) => void localStore.delete(key),
    clear: () => localStore.clear(),
  })

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    useSettingStore.setState({ workspaceId: 'ws1' })
  })

  it('renders mobile panel navigation landmarks', () => {
    render(<WorkflowStudioLayout {...baseProps} />)

    const mobileNav = screen.getByRole('tablist', {
      name: 'Workflow studio panels',
    })
    expect(mobileNav).toBeInTheDocument()
    expect(
      within(mobileNav).getByRole('tab', { name: '画布' })
    ).toBeInTheDocument()
    expect(
      within(mobileNav).getByRole('tab', { name: '编辑节点' })
    ).toBeDisabled()
    expect(within(mobileNav).getByRole('tab', { name: 'Agent' })).toBeEnabled()
  })

  it('renders the empty-state guidance and the workspace editor in empty mode', () => {
    render(
      <WorkflowStudioLayout
        {...baseProps}
        loadState="empty"
        workflow={null}
        revision={null}
        activeRevision={null}
        revisions={[]}
      />
    )

    expect(screen.getByRole('alert')).toHaveTextContent(
      '还没有已发布的 workflow'
    )
    // 空态下编辑区照常渲染，用户直接改模板草稿。
    expect(
      screen.getByRole('tablist', { name: 'Workflow studio panels' })
    ).toBeInTheDocument()
  })

  it('dismisses the empty-state guidance persistently per workspace', () => {
    const emptyProps = {
      ...baseProps,
      loadState: 'empty' as const,
      workflow: null,
      revision: null,
      activeRevision: null,
      revisions: [],
    }
    const { rerender } = render(<WorkflowStudioLayout {...emptyProps} />)

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(
      localStorage.getItem('agent-legion:studio-empty-guide-dismissed:ws1')
    ).toBe('1')
    // 重新渲染（如下次进入页面）也不再出现。
    rerender(<WorkflowStudioLayout {...emptyProps} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('asks for confirmation before applying a chat draft over a dirty editor', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<WorkflowStudioLayout {...baseProps} dirty />)
    const panelProps = chatPanelProps.mock.calls[
      chatPanelProps.mock.calls.length - 1
    ]?.[0] as {
      onApplyWorkflowDraft: (yaml: string) => void
    }

    act(() => panelProps.onApplyWorkflowDraft('key: demo\nlabel: agent\n'))

    expect(confirmSpy).toHaveBeenCalled()
    expect(baseProps.setDefinitionYaml).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    act(() => panelProps.onApplyWorkflowDraft('key: demo\nlabel: agent\n'))
    expect(baseProps.backToDraft).toHaveBeenCalled()
    expect(baseProps.setDefinitionYaml).toHaveBeenCalledWith(
      'key: demo\nlabel: agent\n'
    )
    confirmSpy.mockRestore()
  })

  it('applies a chat draft without confirmation when the editor is clean', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<WorkflowStudioLayout {...baseProps} />)
    const panelProps = chatPanelProps.mock.calls[
      chatPanelProps.mock.calls.length - 1
    ]?.[0] as {
      onApplyWorkflowDraft: (yaml: string) => void
    }

    act(() => panelProps.onApplyWorkflowDraft('key: demo\nlabel: agent\n'))

    expect(confirmSpy).not.toHaveBeenCalled()
    expect(baseProps.setDefinitionYaml).toHaveBeenCalledWith(
      'key: demo\nlabel: agent\n'
    )
    confirmSpy.mockRestore()
  })

  it('opens contextual node editing after a graph node is selected', () => {
    const { rerender } = render(<WorkflowStudioLayout {...baseProps} />)

    const mobileNav = screen.getByRole('tablist', {
      name: 'Workflow studio panels',
    })
    expect(
      within(mobileNav).getByRole('tab', { name: '画布' })
    ).toHaveAttribute('aria-selected', 'true')

    rerender(<WorkflowStudioLayout {...baseProps} selectedNodeKey="node-a" />)

    expect(
      within(mobileNav).getByRole('tab', { name: '编辑节点' })
    ).toHaveAttribute('aria-selected', 'true')
  })
})
