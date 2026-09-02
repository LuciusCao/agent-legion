import { useState } from 'react'
import { Button } from '@mui/material'
import { useStudioState } from '../shared/studioStateContext'
import { AddWorkflowNodeDialog } from './AddWorkflowNodeDialog'

// 工具栏「添加节点」按钮 + 对话框接线（#392 Phase 3）：追加成功后落草稿
// 并选中新节点（inspector 随之打开）。readOnly（历史版本查看）禁用。
export function WorkflowAddNodeButton() {
  const studio = useStudioState()
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button
        size="small"
        disabled={studio.readOnly}
        onClick={() => setOpen(true)}
      >
        添加节点
      </Button>
      <AddWorkflowNodeDialog
        open={open}
        definitionYaml={studio.definitionYaml}
        readOnly={studio.readOnly}
        onClose={() => setOpen(false)}
        onAppended={(yaml, nodeKey) => {
          studio.setDefinitionYaml(yaml)
          studio.setSelectedNodeKey(nodeKey)
          setOpen(false)
        }}
      />
    </>
  )
}
