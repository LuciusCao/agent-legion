import ArticleOutlinedIcon from '@mui/icons-material/ArticleOutlined'
import FolderOpenOutlinedIcon from '@mui/icons-material/FolderOpenOutlined'
import { Button } from '@mui/material'
import { useMemo } from 'react'
import { useParams } from 'react-router-dom'
import type { WorkflowNodeRecord } from '../../types'
import { useShowNodeDetailPreview } from './nodeDetailPreviewContext'
import { useWorkspaceRuntimeModels } from './useWorkspaceRuntimeModels'
import { WorkflowNodeRuntimeSettings } from './WorkflowNodeRuntimeSettings'
import { parseWorkflowExecutionDefaults } from './workflowStudioYamlDraft.executionDefaults'
import styles from './WorkflowAgentExecutionDetails.module.css'

export function WorkflowAgentExecutionDetails(props: {
  node: WorkflowNodeRecord
  runtime: string
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  // 预览在详情 panel 内原位展开（不开 dialog），右侧 Agent 对话保持可见。
  const showPreview = useShowNodeDetailPreview()
  const { workspaceId } = useParams<{ workspaceId: string }>()
  // 「继承默认」提示的来源：草稿 YAML 顶层 execution 块（workspace 级
  // Agent 默认配置已随 schema v63 退役）。全量 YAML parse 按草稿内容 memo，
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
    </>
  )
}
