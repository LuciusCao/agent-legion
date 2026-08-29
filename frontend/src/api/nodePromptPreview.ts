import { api } from './core'
import type { components } from '../generated/api'

export type NodePromptPreviewResponse =
  components['schemas']['NodePromptPreviewResponse']

/** Studio 节点运行 Prompt 预览：definition_yaml 传当前草稿，未保存的编辑即时反映。 */
export async function postNodePromptPreview(
  workspaceId: string,
  nodeKey: string,
  definitionYaml: string
): Promise<NodePromptPreviewResponse> {
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow/node-prompt-preview`,
    {
      method: 'POST',
      body: JSON.stringify({
        node_key: nodeKey,
        definition_yaml: definitionYaml,
      }),
    }
  )
}
