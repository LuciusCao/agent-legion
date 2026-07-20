import ArticleOutlinedIcon from '@mui/icons-material/ArticleOutlined'
import FolderOpenOutlinedIcon from '@mui/icons-material/FolderOpenOutlined'
import { Button } from '@mui/material'
import { useState } from 'react'
import type { WorkflowNodeRecord } from '../../types'
import type { CapabilityBinding } from './WorkflowExecutorBindingList'
import { WorkflowNodeRuntimeSettings } from './WorkflowNodeRuntimeSettings'
import { WorkflowPromptPreviewDialog } from './WorkflowPromptPreviewDialog'
import { WorkflowSkillPreviewDialog } from './WorkflowSkillPreviewDialog'
import { buildWorkflowNodePromptPreview } from './workflowNodePromptPreview'
import { parseWorkflowNode } from './workflowStudioYamlDraft.parse'
import styles from './WorkflowAgentExecutionDetails.module.css'

export function WorkflowAgentExecutionDetails(props: {
  binding: CapabilityBinding
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  const [dialog, setDialog] = useState<'prompt' | 'skill' | null>(null)
  const draft = parseWorkflowNode(props.definitionYaml, props.node.key)
  const additionalPrompt = draft
    ? (draft.execution?.prompt ?? '')
    : (props.node.execution?.prompt ?? '')
  const skillKey = props.binding.detail.skill ?? ''
  return (
    <>
      <div className={styles.actions}>
        <Button
          size="small"
          startIcon={<ArticleOutlinedIcon />}
          onClick={() => setDialog('prompt')}
        >
          查看 Prompt
        </Button>
        <Button
          size="small"
          startIcon={<FolderOpenOutlinedIcon />}
          onClick={() => setDialog('skill')}
        >
          浏览技能文件
        </Button>
      </div>
      <WorkflowNodeRuntimeSettings
        node={props.node}
        defaults={props.binding.detail}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
      <WorkflowPromptPreviewDialog
        open={dialog === 'prompt'}
        nodeLabel={props.node.label}
        prompt={buildWorkflowNodePromptPreview(
          props.node,
          skillKey,
          additionalPrompt
        )}
        onClose={() => setDialog(null)}
      />
      <WorkflowSkillPreviewDialog
        open={dialog === 'skill'}
        skillKey={skillKey}
        onClose={() => setDialog(null)}
      />
    </>
  )
}
