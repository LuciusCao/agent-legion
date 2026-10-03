import { useState } from 'react'
import { WorkflowNodeCodeDialog } from './WorkflowNodeCodeDialog'
import styles from './WorkflowNodeCodePreview.module.css'

type Props = {
  title: string
  code: string
}

/** 「查看代码」入口按钮 + 全屏代码宽视图 dialog（#770 起是栏内看代码的
 * 唯一主路径，窄栏只留摘要）；代码为空（无内置且无草稿）时不渲染。 */
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
        查看代码
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
