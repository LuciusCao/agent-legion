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
    <md-icon-button
      onClick={() => navigate(backTo)}
      aria-label="返回"
      data-testid="app-bar-back"
    >
      <md-icon>arrow_back</md-icon>
    </md-icon-button>
  ) : home ? (
    <md-icon-button
      onClick={() => navigate('/')}
      aria-label="主页"
      data-testid="app-bar-home"
    >
      <md-icon>home</md-icon>
    </md-icon-button>
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
