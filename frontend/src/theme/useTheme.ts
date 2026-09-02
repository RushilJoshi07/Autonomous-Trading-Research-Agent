import { useCallback, useEffect, useState } from 'react'

export type ThemeOverride = 'light' | 'dark' | null

const STORAGE_KEY = 'fathom-theme-override'

/**
 * Mirrors the cascade already proven in the published Fathom design-language
 * artifact: bare `:root` is dark by default, `@media (prefers-color-scheme:
 * light) :root:not([data-theme="light"])` covers a system-light user with no
 * override, and `:root[data-theme="light"|"dark"]` is an explicit override
 * that wins in both directions. The artifact runs inside Claude's own
 * Artifact viewer, which manages that attribute for it -- this app has no
 * such host, so this hook is the standalone equivalent: it owns
 * `document.documentElement.dataset.theme` itself.
 *
 * The override is persisted to localStorage (not sessionStorage) so a
 * deliberate choice survives a closed tab, but nothing here defaults to a
 * remembered override on first load -- with no stored value, the page
 * follows `prefers-color-scheme` exactly as the CSS cascade already does,
 * so a user who never touched the toggle always sees their system theme.
 */
export function useTheme() {
  const [override, setOverrideState] = useState<ThemeOverride>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      return stored === 'light' || stored === 'dark' ? stored : null
    } catch {
      return null
    }
  })

  useEffect(() => {
    if (override === null) {
      delete document.documentElement.dataset.theme
    } else {
      document.documentElement.dataset.theme = override
    }
  }, [override])

  const setOverride = useCallback((next: ThemeOverride) => {
    setOverrideState(next)
    try {
      if (next === null) {
        localStorage.removeItem(STORAGE_KEY)
      } else {
        localStorage.setItem(STORAGE_KEY, next)
      }
    } catch {
      // Private browsing / storage disabled -- the in-memory state above
      // still drives the theme correctly for this session, it just won't
      // survive a reload. Not worth surfacing to the user for a toggle.
    }
  }, [])

  const toggle = useCallback(() => {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    const currentlyDark = override === 'dark' || (override === null && prefersDark)
    setOverride(currentlyDark ? 'light' : 'dark')
  }, [override, setOverride])

  return { override, toggle }
}
