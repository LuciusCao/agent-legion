import { api } from './core'
import type {
  SkillDirectoriesResponse,
  SkillTagsResponse,
  SkillValidateResponse,
} from '../types'

export async function validateSkillPath(
  path: string,
  workspaceId: string
): Promise<SkillValidateResponse> {
  return api(
    `/api/skills/validate?workspace_id=${encodeURIComponent(workspaceId)}`,
    {
      method: 'POST',
      body: JSON.stringify({ path }),
    }
  )
}

export async function fetchSkillTags(
  path: string,
  workspaceId: string
): Promise<SkillTagsResponse> {
  return api(
    `/api/skills/tags?path=${encodeURIComponent(path)}&workspace_id=${encodeURIComponent(workspaceId)}`
  )
}

export async function fetchSkillDirectories(
  workspaceId: string
): Promise<SkillDirectoriesResponse> {
  const query = encodeURIComponent(workspaceId)
  return api(`/api/skills/directories?workspace_id=${query}`)
}
