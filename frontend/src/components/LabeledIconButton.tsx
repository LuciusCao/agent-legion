import { Button, Tooltip, type ButtonProps } from '@mui/material'
import { MaterialIcon } from './MaterialIcon'

/**
 * 顶栏工具按钮：图标 + 短文字名（图标含义不再靠悬停猜）。
 * 与 IconButton 同一视觉层级：无边框文本按钮、继承前景色、紧凑内边距。
 */
export function LabeledIconButton({
  icon,
  label,
  onClick,
  active = false,
  ariaLabel,
  title,
  tooltip,
  disabled = false,
  color,
}: {
  icon: string
  label: string
  onClick?: (event: React.MouseEvent<HTMLButtonElement>) => void
  active?: boolean
  ariaLabel?: string
  /** 原生悬停提示；默认与 ariaLabel / label 一致 */
  title?: string
  /** 一句话说明；传入时用 MUI Tooltip 展示，替代原生 title */
  tooltip?: string
  disabled?: boolean
  /** 不传则继承顶栏前景色 */
  color?: ButtonProps['color']
}) {
  const button = (
    <Button
      size="small"
      onClick={onClick}
      disabled={disabled}
      color={color}
      aria-label={ariaLabel ?? label}
      title={tooltip ? undefined : (title ?? ariaLabel ?? label)}
      className={active ? 'active-icon' : ''}
      startIcon={<MaterialIcon name={icon} sx={{ fontSize: 20 }} />}
      sx={{
        ...(color ? {} : { color: 'inherit' }),
        minWidth: 0,
        px: 1,
        whiteSpace: 'nowrap',
        fontSize: 13,
        '& .MuiButton-startIcon': { marginRight: '4px' },
        '&.Mui-disabled': { color: 'action.disabled' },
      }}
    >
      {label}
    </Button>
  )
  if (!tooltip) return button
  // 禁用的 button 不触发鼠标事件，外包一层 span 让 Tooltip 仍可悬停
  return (
    <Tooltip title={tooltip} arrow enterDelay={300}>
      <span style={{ display: 'inline-flex' }}>{button}</span>
    </Tooltip>
  )
}
