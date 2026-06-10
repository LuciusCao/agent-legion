import {
  createContext,
  useContext,
  useRef,
  useState,
  useCallback,
  useEffect,
  useMemo,
} from 'react'
import styles from './AppShell.module.css'

export interface AppShellState {
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

  const reportedScrolledRef = useRef(reportedScrolled)
  // eslint-disable-next-line react-hooks/refs
  reportedScrolledRef.current = reportedScrolled

  useEffect(() => {
    const main = mainRef.current
    if (!main) return
    const handleScroll = () => {
      if (reportedScrolledRef.current === null) {
        setNativeScrolled(main.scrollTop > 0)
      }
    }
    main.addEventListener('scroll', handleScroll, { passive: true })
    return () => main.removeEventListener('scroll', handleScroll)
  }, [])

  useEffect(() => {
    if (reportedScrolled === null) {
      const main = mainRef.current
      if (main) {
        setNativeScrolled(main.scrollTop > 0)
      }
    }
  }, [reportedScrolled])

  const ctxValue = useMemo(
    () => ({ reportScrolled, resetReportedScroll }),
    [reportScrolled, resetReportedScroll]
  )

  return (
    <AppShellContext.Provider value={ctxValue}>
      <div className={styles.shell}>
        <div className={styles.appBarWrap}>{appBar({ scrolled })}</div>
        <main
          ref={mainRef}
          className={`${styles.main}${mainClassName ? ` ${mainClassName}` : ''}`}
        >
          {children}
        </main>
      </div>
    </AppShellContext.Provider>
  )
}
