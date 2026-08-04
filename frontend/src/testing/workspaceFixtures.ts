import type { AgentStatus, WorkspaceRecord } from '../types'

export function makeWorkspace(
  overrides: Partial<WorkspaceRecord> = {}
): WorkspaceRecord {
  return {
    id: 'ws1',
    name: 'Test Workspace',
    description: '',
    default_workflow_key: 'question_comprehension_info',
    default_entity: 'question',
    resource_config_json: '{}',
    intake_config_json: '{}',
    node_config_json: '{}',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    resource_config: {},
    intake_config: {},
    node_config: {},
    ...overrides,
  }
}

export function makeAgentStatus(
  overrides: Partial<AgentStatus> = {}
): AgentStatus {
  return {
    id: 'pi',
    name: 'Pi Agent',
    workspace_id: 'ws1',
    busy: false,
    task_count: 0,
    max_tasks: 1,
    current_video_id: null,
    current_title: '',
    current_content_type: '',
    current_external_id: '',
    current_phase: '',
    ...overrides,
  }
}
