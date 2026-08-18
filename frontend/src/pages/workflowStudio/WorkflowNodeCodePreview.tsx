import type { components } from '../../generated/api'
import { WorkflowNodeCodeWideView } from './WorkflowNodeCodeWideView'
import sectionStyles from './WorkflowNodeCodeSection.module.css'
import styles from './WorkflowNodeCodePreview.module.css'

type Props = {
  nodeKey: string
  data: components['schemas']['WorkflowNodeCodeResponse']
}

/** 360px 栏内的代码预览：右上角「宽视图」入口 + 滚动 <pre>。
 * 与既有 pre 同一口径：无内置实现时展示未发布草稿。 */
export function WorkflowNodeCodePreview(props: Props) {
  const code =
    props.data.origin === 'none' ? props.data.draft_code : props.data.code
  return (
    <>
      <div className={styles.codeToolbar}>
        <WorkflowNodeCodeWideView
          title={`节点代码 · ${props.nodeKey}`}
          code={code ?? ''}
        />
      </div>
      <pre className={sectionStyles.code}>{code}</pre>
    </>
  )
}
