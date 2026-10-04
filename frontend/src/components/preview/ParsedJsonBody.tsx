/**
 * 合法 JSON 产物的卡片正文：默认树视图，「源码」态展示原文——树视图不画
 * 逗号与键名引号，原文通道让用户能自证产物字节合法（#777）。
 * toggle 复用产物对话框的 ArtifactPreviewModeToggle，两处交互一致。
 */
import { useState } from 'react'
import { JsonTree } from '../JsonTree'
import {
  ArtifactPreviewModeToggle,
  type ArtifactPreviewMode,
} from '../artifact/ArtifactRenderedPreview'
import styles from './previewRenderers.module.css'

export function ParsedJsonBody({
  name,
  content,
  parsed,
}: {
  name: string
  content: string
  parsed: unknown
}) {
  const [mode, setMode] = useState<ArtifactPreviewMode>('rendered')
  return (
    <div>
      <div className={styles.modeBar}>
        <ArtifactPreviewModeToggle name={name} mode={mode} onMode={setMode} />
      </div>
      {mode === 'source' ? (
        <pre className={styles.pre}>{content}</pre>
      ) : (
        <JsonTree data={parsed} />
      )}
    </div>
  )
}
