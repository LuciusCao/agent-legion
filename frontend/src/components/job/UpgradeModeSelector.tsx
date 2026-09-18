import { FormControlLabel, Radio, RadioGroup } from '@mui/material'
import type { UpgradeMode } from '../../types/jobTypes'

export type UpgradeModeSelectorProps = {
  value: UpgradeMode
  onChange: (mode: UpgradeMode) => void
}

/** 升级模式单选（issue #645）：clean = 全量重跑（默认，现状行为）；
 * inherit = 继承未变节点的产物——变更节点及其下游全部重跑（严格传播：
 * 上游重跑即视为输入面变化，不做"是否影响输出"的静态判定）。 */
export function UpgradeModeSelector({
  value,
  onChange,
}: UpgradeModeSelectorProps) {
  return (
    <RadioGroup
      row
      aria-label="升级模式"
      value={value}
      onChange={(e) => onChange(e.target.value as UpgradeMode)}
    >
      <FormControlLabel
        value="clean"
        control={<Radio />}
        label="全量重跑（清空产物）"
      />
      <FormControlLabel
        value="inherit"
        control={<Radio />}
        label="继承未变节点产物（变更节点及其下游全部重跑）"
      />
    </RadioGroup>
  )
}
