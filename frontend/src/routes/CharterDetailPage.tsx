import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { client } from '../api/client'
import type { components } from '../api/schema.gen'
import { CharterSummary } from '../components/CharterSummary'
import { HypothesisRow } from '../components/HypothesisRow'

type CharterRow = components['schemas']['CharterOut']
type Hypothesis = components['schemas']['HypothesisOut']

export function CharterDetailPage() {
  const { charterId } = useParams()
  const [charter, setCharter] = useState<CharterRow | null>(null)
  const [hypotheses, setHypotheses] = useState<Hypothesis[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!charterId) return
    let cancelled = false

    async function load() {
      const [charterRes, hypothesesRes] = await Promise.all([
        client.GET('/charters/{charter_id}', { params: { path: { charter_id: charterId as string } } }),
        client.GET('/hypotheses', { params: { query: { charter_id: charterId as string } } }),
      ])
      if (cancelled) return
      if (charterRes.error || !charterRes.data) {
        setError('Could not reach the backend, or this charter does not exist.')
        return
      }
      setCharter(charterRes.data)
      setHypotheses(hypothesesRes.data ?? [])
    }

    load()
    return () => {
      cancelled = true
    }
  }, [charterId])

  if (error) {
    return <p className="block-body">{error}</p>
  }
  if (!charter || hypotheses === null) {
    return <p className="block-body">Loading charter…</p>
  }

  return (
    <div className="stack">
      <div className="card-top" style={{ marginBottom: -4 }}>
        <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 34, margin: 0 }}>
          {charter.mandate_text}
        </h1>
        <span className={charter.confirmed ? 'pill confirmed' : 'pill inconclusive'}>
          {charter.confirmed ? 'Confirmed' : 'Unconfirmed'}
        </span>
      </div>

      <CharterSummary charter={charter.charter} blocked={false} />

      <div className="card-eyebrow" style={{ marginTop: 8 }}>
        Research log
      </div>

      {!charter.confirmed ? (
        <p className="block-body" style={{ color: 'var(--text-2)' }}>
          This charter isn't confirmed yet, so the agent hasn't started work under
          it — no hypotheses can exist until it is.
        </p>
      ) : hypotheses.length === 0 ? (
        <p className="block-body" style={{ color: 'var(--text-2)' }}>
          No hypotheses yet.
        </p>
      ) : (
        <div className="stack">
          {hypotheses.map((hypothesis) => (
            <HypothesisRow key={hypothesis.id} hypothesis={hypothesis} />
          ))}
        </div>
      )}
    </div>
  )
}
