import { Checkbox, FormControlLabel } from '@mui/material'

/**
 * 创建 workspace 的「从示例模板初始化」开关（schema v50 后的唯一出厂种子）：
 * 勾选后 workspace 以示例 workflow（教学视频脚本与题目生成）的 DAG 与
 * 出厂 Agent 模板初始化；不勾选则从空白画布起步，进 Studio 搭建或让 agent 搭建。
 */
export function SampleTemplateCheckbox({
  checked,
  onChange,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <FormControlLabel
      control={
        <Checkbox
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />
      }
      label="从示例模板初始化（教学视频脚本与题目生成）"
    />
  )
}
