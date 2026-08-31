import { MenuItem, TextField } from '@mui/material'
import type { SkillValidateResponse } from '../types'

type Props = {
  /** 校验成功响应（tags / locked_ref 参考信息来源）。 */
  result: SkillValidateResponse
  selectedTag: string
  tagTouched: boolean
  onSelectTag: (tag: string) => void
}

/** Skill 校验成功后的参考信息区：可用 tag 下拉（纯参考，选择不回写——节点
 * ref 才是版本选择的落点）+ 已锁定版本展示（lock 中唯一 pin tag 时给出）。 */
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
          已锁定版本：{props.result.locked_ref}
        </p>
      )}
      {props.tagTouched && (
        <p style={{ fontSize: 12, color: '#ed6c02' }}>
          此处选择仅作参考：要锁定版本，请在节点 Skill ref 填入该 tag；默认
          latest 跟随仓库最新提交。
        </p>
      )}
    </div>
  )
}
