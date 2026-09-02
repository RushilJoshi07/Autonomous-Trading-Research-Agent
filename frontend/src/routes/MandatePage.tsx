/**
 * Stub for Component 4 (the charter creation flow: the mandate textarea,
 * the confirmation screen, and Component 2's own correct/confirm round
 * trip). This page exists in Component 3 only to prove the "/" route
 * renders inside the shell -- there is deliberately no form here yet.
 */
export function MandatePage() {
  return (
    <div className="stack">
      <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 34 }}>
        Set a research mandate
      </h1>
      <p className="block-body" style={{ color: 'var(--text-2)' }}>
        The mandate form and confirmation flow are Component 4 -- this
        route is a placeholder proving the app shell and routing work.
      </p>
    </div>
  )
}
