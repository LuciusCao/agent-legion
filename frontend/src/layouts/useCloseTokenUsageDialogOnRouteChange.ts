import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'

export function useCloseTokenUsageDialogOnRouteChange(
  open: boolean,
  setOpen: (open: boolean) => void
) {
  const location = useLocation()
  const lastPathnameRef = useRef(location.pathname)
  useEffect(() => {
    if (open && location.pathname !== lastPathnameRef.current) {
      setOpen(false)
    }
    lastPathnameRef.current = location.pathname
  }, [location.pathname, open, setOpen])
}
