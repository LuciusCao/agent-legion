import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Scroll-spy for the settings page sidebar: highlights the nav item of the
 * section currently scrolled into view, and smooth-scrolls to a section on
 * nav click. While a click-initiated smooth scroll is passing through other
 * sections, observer callbacks are briefly suppressed so the clicked item
 * stays highlighted.
 *
 * `sectionsKey` should change whenever the set of rendered sections may have
 * changed (e.g. the nav items memo), so the observer re-attaches.
 */
export function useSettingsScrollSpy(sectionsKey: unknown) {
  const [activeSection, setActiveSection] = useState('basic-info')
  const contentRef = useRef<HTMLDivElement>(null)
  const observerSuppressedUntilRef = useRef(0)

  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return
    const container = contentRef.current
    if (!container) return
    const sections = Array.from(container.querySelectorAll('section[id]'))
    if (sections.length === 0) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (Date.now() < observerSuppressedUntilRef.current) return
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.id)
            break
          }
        }
      },
      { rootMargin: '-80px 0px -60% 0px' }
    )
    sections.forEach((section) => observer.observe(section))
    return () => observer.disconnect()
  }, [sectionsKey])

  const scrollToSection = useCallback((id: string) => {
    setActiveSection(id)
    observerSuppressedUntilRef.current = Date.now() + 800
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  return { activeSection, contentRef, scrollToSection }
}
