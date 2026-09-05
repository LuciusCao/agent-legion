import { MenuItem, TextField } from '@mui/material'

type Props = {
  /** 选中的版本：空/latest = 跟随 HEAD（#322 归一），具体 tag = 冻结。 */
  value: string
  onChange: (ref: string) => void
  /** 选项 tag 列表（版本倒序）；空数组走 latest-only 降级。 */
  tags: string[]
  /** 当前最新 tag（latest 条目与最新 tag 条目的标注来源），无则 null。 */
  latestTag: string | null
  /** 未绑定 skill 时禁用（先经目录名校验选择）。 */
  disabled: boolean
}

/** #410：Skill 绑定的「版本」下拉（自 SkillSelector 拆出，文件预算）——
 * 选项 = latest（动态跟随最新）+ 全部 tag，选择直接写入 skill.ref，替代旧
 * 「可用 tag（参考）」下拉与独立「Skill ref」输入框。空 ref 归一为 latest；
 * tags 为空时降级为 latest 单选 + 说明文案（对齐 WorkflowSkillVersionSelect
 * 的降级策略）；选中的 tag 不在 tags 列表（加载中/已删 tag）时补一项，避免
 * MUI out-of-range 值并保证 pin 的版本始终可见。 */
export function SkillVersionSelect(props: Props) {
  const selectedRef = props.value.trim() || 'latest'
  const options =
    selectedRef !== 'latest' && !props.tags.includes(selectedRef)
      ? [...props.tags, selectedRef]
      : props.tags
  return (
    <TextField
      select
      label="版本"
      variant="outlined"
      value={selectedRef}
      onChange={(e) => props.onChange(e.target.value)}
      fullWidth
      disabled={props.disabled}
      sx={{ mt: 1.5 }}
      helperText={
        props.disabled
          ? '先经上方校验选择 skill'
          : props.tags.length > 0
            ? 'latest = 跟随仓库最新提交；选 tag 锁定版本'
            : '仓库暂无 tag：latest 跟随仓库最新提交'
      }
    >
      <MenuItem value="latest">
        latest
        {props.latestTag
          ? `（当前最新 tag：${props.latestTag}）`
          : '（跟随最新）'}
      </MenuItem>
      {options.map((tag) => (
        <MenuItem key={tag} value={tag}>
          {tag}
          {tag === props.latestTag ? '（最新）' : ''}
        </MenuItem>
      ))}
    </TextField>
  )
}
