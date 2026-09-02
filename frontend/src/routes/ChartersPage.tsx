import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { client } from '../api/client'
import type { components } from '../api/schema.gen'

type CharterOut = components['schemas']['CharterOut']

/**
 * This page is a plumbing proof, not Component 5's real research log --
 * it exists to verify, against the real running backend rather than a
 * mock, that the generated client/CORS/theme/routing all actually work
 * together end to end. It also closes an item Components 1-2 explicitly
 * left open: their CORS middleware allowed localhost:5173 as a disclosed
 * GUESS with no frontend yet to test it against -- this is that test.
 * Component 5 replaces this with the real research-log UI (status pills
 * per hypothesis, expandable verdict cards, the status-poll reveal).
 */
export function ChartersPage() {
  const [charters, setCharters] = useState<CharterOut[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    client.GET('/charters').then(({ data, error }) => {
      if (cancelled) return
      if (error) {
        setError('Could not reach the backend at ' + (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'))
        return
      }
      setCharters(data)
    })
    return () => {
      cancelled = true
    }
  }, [])

  if (error) {
    return <p className="block-body">{error}</p>
  }
  if (charters === null) {
    return <p className="block-body">Loading charters…</p>
  }
  if (charters.length === 0) {
    return <p className="block-body">No charters yet. Set a mandate to create one.</p>
  }

  return (
    <div className="board-wrap">
      <table className="board">
        <thead>
          <tr>
            <th>Mandate</th>
            <th>Status</th>
            <th>Correction round</th>
          </tr>
        </thead>
        <tbody>
          {charters.map((charter) => (
            <tr key={charter.id}>
              <td className="hyp">
                <Link to={`/charters/${charter.id}`}>{charter.mandate_text}</Link>
              </td>
              <td>
                <span className={charter.confirmed ? 'pill confirmed' : 'pill inconclusive'}>
                  {charter.confirmed ? 'Confirmed' : 'Unconfirmed'}
                </span>
              </td>
              <td className="num">{charter.correction_round}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
