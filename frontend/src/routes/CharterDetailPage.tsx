import { useParams } from 'react-router-dom'

/**
 * Stub for Component 5's research log (hypothesis list under a charter,
 * status-poll reveal) and Component 6's trace drill-down. Proves dynamic
 * route params work; nothing here calls the backend yet.
 */
export function CharterDetailPage() {
  const { charterId } = useParams()
  return (
    <div className="stack">
      <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 34 }}>
        Charter {charterId}
      </h1>
      <p className="block-body" style={{ color: 'var(--text-2)' }}>
        The research log (hypotheses, verdict cards, trace drill-down) is
        Components 5-6 -- this route is a placeholder proving dynamic
        params resolve correctly.
      </p>
    </div>
  )
}
