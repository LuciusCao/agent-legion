import ArticleOutlinedIcon from '@mui/icons-material/ArticleOutlined'
import FolderOpenOutlinedIcon from '@mui/icons-material/FolderOpenOutlined'
import { Button } from '@mui/material'
import { useMemo } from 'react'
import { useParams } from 'react-router-dom'
import type { WorkflowNodeRecord } from '../../../types'
import { useShowNodeDetailPreview } from './nodeDetailPreviewContext'
import { useWorkspaceRuntimeModels } from '../shared/useWorkspaceRuntimeModels'
import { WorkflowNodeRuntimeSettings } from './WorkflowNodeRuntimeSettings'
import { WorkflowNodeToolsEditor } from './WorkflowNodeToolsEditor'
import { parseWorkflowExecutionDefaults } from '../shared/workflowStudioYamlDraft.executionDefaults'
import styles from './WorkflowAgentExecutionDetails.module.css'

export function WorkflowAgentExecutionDetails(props: {
  node: WorkflowNodeRecord
  runtime: string
  /** #575：Agent 定义层的兜底 tools，透传给节点级编辑器做生效值提示。
   *  undefined = 未知（draft-only Agent 的列表映射不含 tools，#387），
   *  此时不出 hint——未知与「定义确为空」必须区分（codex P2 on #580）。 */
  agentDefaultTools?: string[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  // 预览在详情 panel 内原位展开（不开 dialog），右侧 Agent 对话保持可见。
  const showPreview = useShowNodeDetailPreview()
  const { workspaceId } = useParams<{ workspaceId: string }>()
  // 「继承默认」提示的来源：草稿 YAML 顶层 execution 块（workspace 级
  // Agent 默认配置已随 schema v64 退役）。全量 YAML parse 按草稿内容 memo，
  // 不随每次渲染重算。
  const defaults = useMemo(
    () => parseWorkflowExecutionDefaults(props.definitionYaml),
    [props.definitionYaml]
  )
  return (
    <>
      <div className={styles.actions}>
        <Button
          size="small"
          startIcon={<ArticleOutlinedIcon />}
          onClick={() => showPreview('prompt')}
        >
          查看 Prompt
        </Button>
        <Button
          size="small"
          startIcon={<FolderOpenOutlinedIcon />}
          onClick={() => showPreview('skill')}
        >
          浏览技能文件
        </Button>
      </div>
      <WorkflowNodeRuntimeSettings
        node={props.node}
        runtime={props.runtime}
        defaults={defaults}
        runtimeModels={useWorkspaceRuntimeModels(workspaceId).data?.runtimes}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
      {/* #443/#476：节点级 tools 声明编辑入口（选项与 AgentEditor 同源）。 */}
      <WorkflowNodeToolsEditor
        node={props.node}
        runtime={props.runtime}
        agentDefaultTools={props.agentDefaultTools}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
    </>
  )
}
