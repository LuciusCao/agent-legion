import { useEffect, useState } from 'react'

export function useAppBarBottom(): number {
  const [bottom, setBottom] = useState(0)
  useEffect(() => {
    const update = () => {
      const appBar = document.querySelector('[data-testid="app-bar"]')
      setBottom(appBar ? appBar.getBoundingClientRect().bottom : 0)
    }
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])
  return bottom
}
