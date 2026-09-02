import { NavLink, Outlet } from 'react-router-dom'
import { useTheme } from '../theme/useTheme'

const navLinkClassName = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'shell-nav-link shell-nav-link-active' : 'shell-nav-link'

export function Layout() {
  const { override, toggle } = useTheme()
  const isDark =
    override === 'dark' ||
    (override === null && window.matchMedia('(prefers-color-scheme: dark)').matches)

  return (
    <>
      <header className="shell-header">
        <span className="shell-wordmark">Fathom</span>
        <nav className="shell-nav">
          <NavLink to="/" end className={navLinkClassName}>
            New mandate
          </NavLink>
          <NavLink to="/charters" className={navLinkClassName}>
            Charters
          </NavLink>
          <NavLink to="/scoreboard" className={navLinkClassName}>
            Scoreboard
          </NavLink>
        </nav>
        <button type="button" className="shell-theme-toggle" onClick={toggle}>
          {isDark ? 'Light mode' : 'Dark mode'}
        </button>
      </header>
      <main className="page">
        <Outlet />
      </main>
    </>
  )
}
