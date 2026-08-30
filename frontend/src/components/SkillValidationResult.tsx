import { MenuItem, TextField } from '@mui/material'
import type { SkillValidateResponse } from '../types'

type Props = {
  /** 校验成功响应（tags / locked_ref 参考信息来源）。 */
  result: SkillValidateResponse
  selectedTag: string
  tagTouched: boolean
  onSelectTag: (tag: string) => void
}

/** Skill 校验成功后的参考信息区：可用 tag 下拉（纯参考，选择不回写——DB
 * skill_lock 仍是锁定 ref 的唯一权威）+ 当前锁定 ref + 同步流程提示。 */
export function SkillValidationResult(props: Props) {
  const tags = props.result.tags ?? []
  return (
    <div style={{ marginTop: 12 }}>
      {tags.length > 0 && (
        <TextField
          select
          label="可用 tag（参考）"
          variant="outlined"
          value={props.selectedTag}
          onChange={(e) => props.onSelectTag(e.target.value)}
          fullWidth
        >
          {tags.map((tag) => (
            <MenuItem key={tag} value={tag}>
              {tag}
              {tag === props.result.latest_tag ? '（最新）' : ''}
            </MenuItem>
          ))}
        </TextField>
      )}
      {props.result.locked_ref && (
        <p style={{ fontSize: 12, color: '#616161' }}>
          当前锁定 ref：{props.result.locked_ref}
        </p>
      )}
      {props.tagTouched && (
        <p style={{ fontSize: 12, color: '#ed6c02' }}>
          tag 变更需通过 skills 同步流程生效，此处选择不会修改锁定 ref。
        </p>
      )}
    </div>
  )
}
