import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import { Button } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getSkillDetail } from '../../../api/agentCatalogApi'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { WorkflowSkillFileList } from './WorkflowSkillFileList'
import { WorkflowSkillVersionSelect } from './WorkflowSkillVersionSelect'
import styles from './WorkflowSkillPreviewPanel.module.css'

/** 详情 panel 内的技能文件预览（原位替换 inspector，不开 dialog）：
 * 左侧文件列表 + 右侧内容，顶部版本下拉（tags 来自预览响应）切换查看版本。
 * Studio 对话 turn_end 会按 'studioSkillDetail' 前缀失效本查询（useStudioChat），
 * agent 修改技能文件后 panel 自动刷新。 */
export function WorkflowSkillPreviewPanel(props: {
  skillKey: string
  onBack: () => void
}) {
  // 版本选择带 skillKey 印记：切换节点/技能绑定即回落锁定版本（组件通常随
  // 节点切换卸载重建，印记兜底复用场景）。
  const [selection, setSelection] = useState<{
    key: string
    ref: string
  } | null>(null)
  const ref = selection?.key === props.skillKey ? selection.ref : null
  const [selectedPath, setSelectedPath] = useState('SKILL.md')
  const query = useQuery({
    queryKey: extraQueryKeys.studioSkillDetail(props.skillKey, ref),
    queryFn: () => getSkillDetail(props.skillKey, ref ?? undefined),
    enabled: Boolean(props.skillKey),
  })
  const detail = query.data ?? null
  const files = detail?.files ?? []
  const selected = files.find((file) => file.path === selectedPath) ?? files[0]
  const error =
    query.error instanceof Error
      ? query.error.message
      : query.isError
        ? '加载失败'
        : ''
  return (
    <section aria-label="技能文件预览" className={styles.panel}>
      <div className={styles.header}>
        <Button
          size="small"
          startIcon={<ArrowBackIcon />}
          onClick={props.onBack}
        >
          返回节点详情
        </Button>
        <span className={styles.title}>{props.skillKey || '未绑定技能'}</span>
        <WorkflowSkillVersionSelect
          skillKey={props.skillKey}
          viewingRef={ref}
          detail={detail}
          onSelect={(next) =>
            setSelection(next ? { key: props.skillKey, ref: next } : null)
          }
        />
      </div>
      <div className={styles.content}>
        <WorkflowSkillFileList
          files={files}
          selected={selected}
          onSelect={setSelectedPath}
        />
        <div className={styles.preview}>
          {error && <div className={styles.state}>{error}</div>}
          {!error && !detail && (
            <div className={styles.state}>正在加载技能文件...</div>
          )}
          {detail && files.length === 0 && (
            <div className={styles.state}>本地技能目录不可用</div>
          )}
          {selected && <pre className={styles.code}>{selected.content}</pre>}
        </div>
      </div>
    </section>
  )
}
