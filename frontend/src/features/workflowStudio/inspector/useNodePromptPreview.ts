import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { postNodePromptPreview } from '../../../api/nodePromptPreview'
import { useDebouncedCallback } from '../../../hooks/useDebouncedCallback'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import type { WorkflowNodeRecord } from '../../../types'
import type { NodePromptEditorProps } from './WorkflowNodePromptEditor'
import { patchWorkflowNodeExecution } from '../shared/workflowStudioYamlDraft.execution'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft.parse'

const PREVIEW_DEBOUNCE_MS = 300

export type NodePromptPreviewPanelProps = {
  node: WorkflowNodeRecord
  /** agentCatalog 绑定的技能（预览响应缺失 skill_key 时兜底）。 */
  fallbackSkillKey: string
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

/** 运行 Prompt 面板的数据层：预览走后端预览 API，草稿 YAML 按 300ms
 * debounce 参与请求（打字期间不每键一发，未保存的编辑即时反映）。编辑值以
 * 草稿 YAML 为准（即时），默认指令文本来自预览响应（滞后一拍可接受）。 */
export function useNodePromptPreview(props: NodePromptPreviewPanelProps): {
  editor: NodePromptEditorProps
  effectivePrompt: string | null
} {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  // 初值即当前草稿，首次加载不等 debounce。
  const [debouncedYaml, setDebouncedYaml] = useState(props.definitionYaml)
  const scheduleYaml = useDebouncedCallback(
    setDebouncedYaml,
    PREVIEW_DEBOUNCE_MS
  )
  useEffect(() => {
    scheduleYaml(props.definitionYaml)
  }, [props.definitionYaml, scheduleYaml])
  const query = useQuery({
    queryKey: extraQueryKeys.studioNodePromptPreview(
      workspaceId ?? '',
      props.node.key,
      debouncedYaml
    ),
    queryFn: () =>
      postNodePromptPreview(workspaceId ?? '', props.node.key, debouncedYaml),
    enabled: Boolean(workspaceId),
  })
  const preview = query.data ?? null

  const draft = parseWorkflowNode(props.definitionYaml, props.node.key)
  const customPrompt = draft
    ? (draft.execution?.prompt ?? '')
    : (props.node.execution?.prompt ?? '')
  const isDefault = customPrompt.trim() === ''
  // skill_key 以后端预览响应为准（显式 null = 未绑定）；响应未返回前用
  // agentCatalog 绑定兜底。
  const skillKey = preview
    ? (preview.skill_key ?? null)
    : props.fallbackSkillKey || null
  return {
    editor: {
      isDefault,
      instructions: isDefault
        ? (preview?.default_instructions ?? '')
        : customPrompt,
      skillKey,
      previewError:
        query.error instanceof Error
          ? query.error.message
          : query.isError
            ? '加载失败'
            : '',
      loading: !preview,
      readOnly: Boolean(props.readOnly),
      onPatch: (value) =>
        props.setDefinitionYaml(
          patchWorkflowNodeExecution(
            props.definitionYaml,
            props.node.key,
            'prompt',
            value
          )
        ),
    },
    effectivePrompt: preview?.effective_prompt ?? null,
  }
}
