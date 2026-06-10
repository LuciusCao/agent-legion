import {
  createContext,
  useContext,
  useRef,
  useState,
  useCallback,
  useEffect,
} from 'react'
import styles from './AppShell.module.css'

interface AppShellState {
  scrolled: boolean
}

interface AppShellContextValue {
  reportScrolled: (scrolled: boolean) => void
  resetReportedScroll: () => void
}

const AppShellContext = createContext<AppShellContextValue | null>(null)

export function useAppShellScroll(): AppShellContextValue {
  const ctx = useContext(AppShellContext)
  if (!ctx) {
    throw new Error('useAppShellScroll must be used inside AppShell')
  }
  return ctx
}

export interface AppShellProps {
  appBar: (state: AppShellState) => React.ReactNode
  children: React.ReactNode
  mainClassName?: string
}

export function AppShell({ appBar, children, mainClassName }: AppShellProps) {
  const mainRef = useRef<HTMLElement>(null)
  const [nativeScrolled, setNativeScrolled] = useState(false)
  const [reportedScrolled, setReportedScrolled] = useState<boolean | null>(null)

  const scrolled = reportedScrolled !== null ? reportedScrolled : nativeScrolled

  const reportScrolled = useCallback((value: boolean) => {
    setReportedScrolled(value)
  }, [])

  const resetReportedScroll = useCallback(() => {
    setReportedScrolled(null)
  }, [])

  useEffect(() => {
    const main = mainRef.current
    if (!main) return
    const handleScroll = () => {
      if (reportedScrolled === null) {
        setNativeScrolled(main.scrollTop > 0)
      }
    }
    main.addEventListener('scroll', handleScroll, { passive: true })
    return () => main.removeEventListener('scroll', handleScroll)
  }, [reportedScrolled])

  return (
    <AppShellContext.Provider value={{ reportScrolled, resetReportedScroll }}>
      <div className={styles.shell}>
        <div className={styles.appBarWrap}>{appBar({ scrolled })}</div>
        <main ref={mainRef} className={`${styles.main} ${mainClassName ?? ''}`}>
          {children}
        </main>
      </div>
    </AppShellContext.Provider>
  )
}
