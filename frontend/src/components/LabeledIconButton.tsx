import { Button } from '@mui/material'
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
}: {
  icon: string
  label: string
  onClick?: () => void
  active?: boolean
  ariaLabel?: string
}) {
  return (
    <Button
      size="small"
      onClick={onClick}
      aria-label={ariaLabel ?? label}
      className={active ? 'active-icon' : ''}
      startIcon={<MaterialIcon name={icon} sx={{ fontSize: 20 }} />}
      sx={{
        color: 'inherit',
        minWidth: 0,
        px: 1,
        whiteSpace: 'nowrap',
        fontSize: 13,
        '& .MuiButton-startIcon': { marginRight: '4px' },
      }}
    >
      {label}
    </Button>
  )
}
