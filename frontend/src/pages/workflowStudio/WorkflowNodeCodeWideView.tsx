import { useState } from 'react'
import { WorkflowNodeCodeDialog } from './WorkflowNodeCodeDialog'
import styles from './WorkflowNodeCodePreview.module.css'

type Props = {
  title: string
  code: string
}

/** 「宽视图」入口按钮 + 全屏代码 dialog；代码为空（无内置且无草稿）时不渲染。 */
export function WorkflowNodeCodeWideView(props: Props) {
  const [open, setOpen] = useState(false)
  if (!props.code) return null
  return (
    <>
      <button
        type="button"
        className={styles.wideViewButton}
        onClick={() => setOpen(true)}
      >
        宽视图
      </button>
      <WorkflowNodeCodeDialog
        open={open}
        title={props.title}
        code={props.code}
        onClose={() => setOpen(false)}
      />
    </>
  )
}
