import ArticleOutlinedIcon from '@mui/icons-material/ArticleOutlined'
import FolderOpenOutlinedIcon from '@mui/icons-material/FolderOpenOutlined'
import { Button } from '@mui/material'
import type { WorkflowNodeRecord } from '../../types'
import { useShowNodeDetailPreview } from './nodeDetailPreviewContext'
import { useWorkspaceAgentDefaults } from './useWorkspaceAgentDefaults'
import { WorkflowNodeRuntimeSettings } from './WorkflowNodeRuntimeSettings'
import styles from './WorkflowAgentExecutionDetails.module.css'

export function WorkflowAgentExecutionDetails(props: {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  // 预览在详情 panel 内原位展开（不开 dialog），右侧 Agent 对话保持可见。
  const showPreview = useShowNodeDetailPreview()
  const agentDefaults = useWorkspaceAgentDefaults()
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
        defaults={agentDefaults ?? {}}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
    </>
  )
}
