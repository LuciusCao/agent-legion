import type { AgentStatus, WorkspaceRecord } from '../types'

export function makeWorkspace(
  overrides: Partial<WorkspaceRecord> = {}
): WorkspaceRecord {
  return {
    id: 'ws1',
    name: 'Test Workspace',
    description: '',
    default_workflow_key: 'demo_workflow',
    default_entity: 'question',
    resource_config_json: '{}',
    node_config_json: '{}',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    resource_config: {},
    node_config: {},
    ...overrides,
  }
}

export function makeAgentStatus(
  overrides: Partial<AgentStatus> = {}
): AgentStatus {
  return {
    id: 'worker-1',
    name: 'Worker',
    workspace_id: 'ws1',
    busy: false,
    task_count: 0,
    max_tasks: 1,
    ...overrides,
  }
}
