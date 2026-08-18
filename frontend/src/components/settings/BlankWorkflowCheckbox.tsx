import { Checkbox, FormControlLabel } from '@mui/material'

/** 创建 workspace 的「空白（从零搭建）」开关：跳过 demo seed，Studio 从模板空草稿起步。 */
export function BlankWorkflowCheckbox({
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
      label="空白（从零搭建）"
    />
  )
}
