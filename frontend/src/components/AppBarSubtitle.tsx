import type { ReactNode } from 'react'
import styles from './AppBarSubtitle.module.css'

export interface AppBarSubtitleProps {
  title: string
  subtitle?: ReactNode | null
}

export function AppBarSubtitle({ title, subtitle }: AppBarSubtitleProps) {
  return (
    <div className={styles.wrap}>
      <h1 className={styles.title}>{title}</h1>
      {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
    </div>
  )
}
