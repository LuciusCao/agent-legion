import { IconButton } from '@mui/material'
import { MaterialIcon } from './MaterialIcon'
import { useNavigate } from 'react-router-dom'
import styles from './AppBar.module.css'

export interface AppBarProps {
  title: string
  home?: boolean
  backTo?: string
  scrolled?: boolean
  rightActions?: React.ReactNode
}

export function AppBar({
  title,
  home,
  backTo,
  scrolled,
  rightActions,
}: AppBarProps) {
  const navigate = useNavigate()

  const leftButton = backTo ? (
    <IconButton
      onClick={() => navigate(backTo)}
      aria-label="返回"
      data-testid="app-bar-back"
    >
      <MaterialIcon name="arrow_back" />
    </IconButton>
  ) : home ? (
    <IconButton
      onClick={() => navigate('/')}
      aria-label="主页"
      data-testid="app-bar-home"
    >
      <MaterialIcon name="home" />
    </IconButton>
  ) : null

  return (
    <header
      className={`${styles.appBar} ${scrolled ? styles.scrolled : ''}`}
      data-testid="app-bar"
    >
      <div className={styles.left}>
        {leftButton}
        <h1 className={styles.title}>{title}</h1>
      </div>
      {rightActions && <div className={styles.right}>{rightActions}</div>}
    </header>
  )
}
