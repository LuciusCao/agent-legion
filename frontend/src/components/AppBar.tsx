import { IconButton } from '@mui/material'
import { MaterialIcon } from './MaterialIcon'
import { useNavigate } from 'react-router-dom'
import { AppBarSubtitle } from './AppBarSubtitle'
import styles from './AppBar.module.css'

export interface AppBarProps {
  title: string
  subtitle?: React.ReactNode | null
  home?: boolean
  backTo?: string
  scrolled?: boolean
  rightActions?: React.ReactNode
}

export function AppBar({
  title,
  subtitle,
  home,
  backTo,
  scrolled,
  rightActions,
}: AppBarProps) {
  const navigate = useNavigate()

  const leftButton =
    backTo || home ? (
      <IconButton
        size="small"
        onClick={() => navigate(backTo || '/')}
        aria-label={backTo ? '返回' : '主页'}
        data-testid={backTo ? 'app-bar-back' : 'app-bar-home'}
      >
        <MaterialIcon name={backTo ? 'arrow_back' : 'home'} />
      </IconButton>
    ) : null

  return (
    <header
      className={`${styles.appBar} ${scrolled ? styles.scrolled : ''}`}
      data-testid="app-bar"
    >
      <div className={styles.left}>
        {leftButton}
        <AppBarSubtitle title={title} subtitle={subtitle} />
      </div>
      {rightActions && <div className={styles.right}>{rightActions}</div>}
    </header>
  )
}
