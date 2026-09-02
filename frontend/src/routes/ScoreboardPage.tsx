/**
 * Stub for Component 7's scoreboard (confirmed / decayed / testing,
 * derived from GET /scoreboard). Placeholder only.
 */
export function ScoreboardPage() {
  return (
    <div className="stack">
      <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 34 }}>
        Scoreboard
      </h1>
      <p className="block-body" style={{ color: 'var(--text-2)' }}>
        The confirmed/decayed/testing scoreboard is Component 7 -- this
        route is a placeholder proving the nav link and route render.
      </p>
    </div>
  )
}
