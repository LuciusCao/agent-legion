import type { components } from '../../../generated/api'
import { summarizeNodeCode } from './nodeCodeSummary'
import { WorkflowNodeCodeWideView } from './WorkflowNodeCodeWideView'
import styles from './WorkflowNodeCodeSummary.module.css'

type Props = {
  nodeKey: string
  data: components['schemas']['WorkflowNodeCodeResponse']
}

/** 抽屉栏内的节点代码摘要（#770：窄栏不再内嵌滚动 <pre>——长代码在栏宽
 * 里横纵双滚动、每行只露十几个字符）：只给行数 + 顶层签名（入口函数在前），
 * 「查看代码」一键进宽视图（全屏、行号、高亮）。与既有口径一致：无内置
 * 实现时摘要的是未发布草稿。 */
export function WorkflowNodeCodePreview(props: Props) {
  const code =
    (props.data.origin === 'none' ? props.data.draft_code : props.data.code) ??
    ''
  const summary = summarizeNodeCode(code)
  if (summary.lineCount === 0)
    return <div className={styles.summary}>暂无代码</div>
  return (
    <div className={styles.summary} aria-label="节点代码摘要">
      <div className={styles.summaryHeader}>
        <span className={styles.lineCount}>{summary.lineCount} 行</span>
        <WorkflowNodeCodeWideView
          title={`节点代码 · ${props.nodeKey}`}
          code={code}
        />
      </div>
      {summary.signatures.length > 0 ? (
        <ul className={styles.signatures}>
          {summary.signatures.map((signature) => (
            <li key={signature} className={styles.signature} title={signature}>
              {signature === summary.entrypoint ? (
                <span className={styles.entryBadge}>入口</span>
              ) : null}
              <code>{signature}</code>
            </li>
          ))}
          {summary.hiddenCount > 0 ? (
            <li className={styles.more}>另有 {summary.hiddenCount} 个定义</li>
          ) : null}
        </ul>
      ) : (
        <div className={styles.more}>未识别到顶层函数或类定义</div>
      )}
    </div>
  )
}
