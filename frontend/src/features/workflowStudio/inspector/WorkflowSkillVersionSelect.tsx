import { MenuItem, TextField } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import type { SkillDetail } from '../../../types/executorTypes'
import styles from './WorkflowSkillPreviewPanel.module.css'

/** 技能预览的版本选择：数据源为预览响应的 tags（skill repo 全部 git tag，
 * 版本倒序）；空 tags / 字段缺失时降级为纯文本版本显示。首项始终是「当前
 * 锁定版本」（不带 ref 的默认响应），选中 tag 经 onSelect 触发带 ?ref= 的
 * 重新拉取。锁定 ref 从锁定查询缓存取，切到 tag 后标签保持稳定。 */
export function WorkflowSkillVersionSelect(props: {
  skillKey: string
  viewingRef: string | null
  detail: SkillDetail | null
  onSelect: (ref: string | null) => void
}) {
  const queryClient = useQueryClient()
  const detail = props.detail
  const tags = detail?.tags ?? []
  if (tags.length === 0) {
    if (!detail) return null
    const version = `${detail.ref || '未知版本'} · ${detail.commit.slice(0, 7)}`
    return <span className={styles.version}>{version}</span>
  }
  const locked = queryClient.getQueryData<SkillDetail>(
    extraQueryKeys.studioSkillDetail(props.skillKey, null)
  )
  // getQueryData 非响应式：锁定条目 gcTime 过期后标签退化为「当前锁定版本」
  // 纯文本（不带 ref 名），评审确认可接受——选中状态与内容不受缓存存活影响。
  const lockedLabel = locked?.ref
    ? `当前锁定版本（${locked.ref}）`
    : '当前锁定版本'
  return (
    <TextField
      select
      size="small"
      label="版本"
      className={styles.versionSelect}
      value={props.viewingRef ?? ''}
      onChange={(event) => props.onSelect(event.target.value || null)}
    >
      <MenuItem value="">{lockedLabel}</MenuItem>
      {tags.map((tag) => (
        <MenuItem key={tag} value={tag}>
          {tag}
        </MenuItem>
      ))}
    </TextField>
  )
}
