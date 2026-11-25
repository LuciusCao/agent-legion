import type {
  SharedResource,
  SharedResourceVersion,
} from '../features/sharedResources/sharedResourceTypes'

export function makeSharedResource(
  overrides: Partial<SharedResource> = {}
): SharedResource {
  return {
    id: 'resource-1',
    name: '通用审核规则',
    owner_workspace_id: 'ws1',
    is_owner: true,
    access_status: 'active',
    version_count: 1,
    current_selection: {
      version_id: 'version-1',
      version: 1,
      manifest_hash: 'a'.repeat(64),
      revision: 3,
      selected_by: 'admin',
      updated_at: '2026-01-01T00:00:00Z',
    },
    created_by: 'admin',
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export function makeSharedResourceVersion(
  overrides: Partial<SharedResourceVersion> = {}
): SharedResourceVersion {
  return {
    id: 'version-1',
    resource_id: 'resource-1',
    version: 1,
    manifest_hash: 'a'.repeat(64),
    source: {
      type: 'material',
      id: 'material-1',
      workspace_id: 'ws1',
      materials: [],
    },
    file_count: 1,
    total_size_bytes: 42,
    created_by: 'admin',
    created_at: '2026-01-01T00:00:00Z',
    ready_at: '2026-01-01T00:00:00Z',
    files: [
      {
        path: 'rules.md',
        sha256: 'a'.repeat(64),
        size_bytes: 42,
        content_type: 'text/markdown',
      },
    ],
    ...overrides,
  }
}
