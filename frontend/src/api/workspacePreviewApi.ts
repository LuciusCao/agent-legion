/**
 * workspace 预览配置 API（拆出 workspaceApi 以过架构文件预算）。
 */
import { api } from './core'
import type { WorkspaceSettingsResponse } from '../types'

/**
 * workspace 级产物预览隐藏列表（job 详情左栏勾选）。PATCH settings/preview：
 * 服务端会去重排序；空数组 = 全部显示。
 */
export async function updateWorkspacePreviewHidden(
  workspaceId: string,
  previewHidden: string[]
): Promise<WorkspaceSettingsResponse> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/settings/preview`,
    {
      method: 'PATCH',
      body: JSON.stringify({ previewHidden }),
    }
  )
}
