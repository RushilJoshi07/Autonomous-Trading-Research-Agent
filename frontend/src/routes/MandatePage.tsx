import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { client } from '../api/client'
import type { components } from '../api/schema.gen'
import { CharterSummary } from '../components/CharterSummary'

type CharterRow = components['schemas']['CharterOut']

// Mirrors agentic_core.charter.MAX_CORRECTION_ROUNDS. Not exposed over the
// API today (the backend enforces it; the row's own correction_round tells
// a client how many rounds it has already used), so this is a disclosed
// duplication of the same number rather than something derived from a
// response -- see the walkthrough for why that's an acceptable, named gap
// rather than a hidden one.
const MAX_CORRECTION_ROUNDS = 2

type Phase = 'entry' | 'reviewing' | 'confirmed'

function extractErrorDetail(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'detail' in error) {
    const detail = (error as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d)))
        .join('; ')
    }
  }
  return fallback
}

export function MandatePage() {
  const [phase, setPhase] = useState<Phase>('entry')
  const [row, setRow] = useState<CharterRow | null>(null)
  const [blocked, setBlocked] = useState(false)
  const [mandateText, setMandateText] = useState('')
  const [isCorrecting, setIsCorrecting] = useState(false)
  const [correctionText, setCorrectionText] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  async function handleSubmitMandate(e: FormEvent) {
    e.preventDefault()
    const text = mandateText.trim()
    if (!text) return
    setIsSubmitting(true)
    setErrorMessage(null)
    const { data, error } = await client.POST('/charters', { body: { mandate_text: text } })
    setIsSubmitting(false)
    if (error || !data) {
      setErrorMessage(extractErrorDetail(error, 'Could not parse this mandate. Try again.'))
      return
    }
    setRow(data)
    setBlocked(data.blocked)
    setPhase('reviewing')
  }

  async function handleSubmitCorrection(e: FormEvent) {
    e.preventDefault()
    if (!row) return
    const text = correctionText.trim()
    if (!text) return
    setIsSubmitting(true)
    setErrorMessage(null)
    const { data, error } = await client.POST('/charters/{charter_id}/correct', {
      params: { path: { charter_id: row.id } },
      body: { correction_text: text },
    })
    setIsSubmitting(false)
    if (error || !data) {
      setErrorMessage(extractErrorDetail(error, 'Could not apply that correction.'))
      return
    }
    setRow(data)
    setBlocked(data.blocked)
    setCorrectionText('')
    setIsCorrecting(false)
  }

  async function handleConfirm() {
    if (!row) return
    setIsSubmitting(true)
    setErrorMessage(null)
    const { data, error } = await client.POST('/charters/{charter_id}/confirm', {
      params: { path: { charter_id: row.id } },
    })
    setIsSubmitting(false)
    if (error || !data) {
      setErrorMessage(extractErrorDetail(error, 'Could not confirm this charter.'))
      return
    }
    setRow(data)
    setPhase('confirmed')
  }

  function handleStartOver() {
    setRow(null)
    setBlocked(false)
    setMandateText('')
    setIsCorrecting(false)
    setCorrectionText('')
    setErrorMessage(null)
    setPhase('entry')
  }

  return (
    <div className="stack">
      <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 34 }}>
        Set a research mandate
      </h1>

      {phase === 'entry' && (
        <form className="stack" onSubmit={handleSubmitMandate}>
          <p className="block-body" style={{ color: 'var(--text-2)' }}>
            Describe a research direction in plain language. The agent generates its
            own hypotheses under it once you confirm.
          </p>
          {errorMessage && (
            <p className="block-body" style={{ color: 'var(--rejected)' }}>
              {errorMessage}
            </p>
          )}
          <label className="field">
            <span className="card-eyebrow">Research mandate</span>
            <textarea
              className="textarea"
              value={mandateText}
              onChange={(e) => setMandateText(e.target.value)}
              placeholder='e.g. "Investigate mean-reversion on liquid tech names, daily. Prefer robustness over raw returns."'
              rows={5}
              disabled={isSubmitting}
            />
          </label>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={isSubmitting || !mandateText.trim()}
            style={{ alignSelf: 'flex-start' }}
          >
            {isSubmitting ? 'Parsing…' : 'Propose charter'}
          </button>
        </form>
      )}

      {phase === 'reviewing' && row && (
        <div className="stack">
          <CharterSummary charter={row.charter} blocked={blocked} />

          <div className="card">
            <div className="card-eyebrow">
              Correction round {row.correction_round} of {MAX_CORRECTION_ROUNDS}
            </div>

            {blocked && (
              <p className="block-body" style={{ color: 'var(--text-2)' }}>
                The resolved universe is empty. Check the sector/industry values
                above against what's actually in the database, then request a
                correction below.
              </p>
            )}

            {errorMessage && (
              <p className="block-body" style={{ color: 'var(--rejected)' }}>
                {errorMessage}
              </p>
            )}

            {!isCorrecting ? (
              <div className="pill-row" style={{ marginTop: 14, alignItems: 'center' }}>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={blocked || isSubmitting}
                  onClick={handleConfirm}
                >
                  {isSubmitting ? 'Confirming…' : 'Confirm this charter'}
                </button>

                {row.correction_round < MAX_CORRECTION_ROUNDS ? (
                  <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={isSubmitting}
                    onClick={() => setIsCorrecting(true)}
                  >
                    Request a correction ({MAX_CORRECTION_ROUNDS - row.correction_round} left)
                  </button>
                ) : (
                  <span className="block-body" style={{ color: 'var(--text-3)', fontSize: 13, maxWidth: 'none' }}>
                    No corrections remain — confirm as-is or start over.
                  </span>
                )}

                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={isSubmitting}
                  onClick={handleStartOver}
                >
                  Start over
                </button>
              </div>
            ) : (
              <form className="stack" style={{ marginTop: 14 }} onSubmit={handleSubmitCorrection}>
                <label className="field">
                  <span className="card-eyebrow">What should change?</span>
                  <textarea
                    className="textarea"
                    value={correctionText}
                    onChange={(e) => setCorrectionText(e.target.value)}
                    placeholder='e.g. "too narrow, widen it to all of Technology, not just Consumer Electronics"'
                    rows={3}
                    disabled={isSubmitting}
                  />
                </label>
                <div className="pill-row">
                  <button
                    type="submit"
                    className="btn btn-primary"
                    disabled={isSubmitting || !correctionText.trim()}
                  >
                    {isSubmitting ? 'Submitting…' : 'Submit correction'}
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    disabled={isSubmitting}
                    onClick={() => {
                      setIsCorrecting(false)
                      setCorrectionText('')
                      setErrorMessage(null)
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {phase === 'confirmed' && row && (
        <div className="stack">
          <CharterSummary charter={row.charter} blocked={false} />
          <div className="card">
            <div className="pill-row" style={{ marginBottom: 14 }}>
              <span className="pill confirmed">Confirmed</span>
            </div>
            <p className="block-body" style={{ color: 'var(--text-2)' }}>
              This charter is confirmed and locked. Corrections (what you just used,
              if any) only apply <strong style={{ color: 'var(--text-1)' }}>before</strong>{' '}
              confirmation. Changing direction on a confirmed charter — asking
              follow-up questions or redirecting it based on results — is a separate
              feature (architecture.md Step 7) that hasn't been built yet.
            </p>
            <div className="pill-row">
              <Link className="btn btn-secondary" to={`/charters/${row.id}`}>
                View charter
              </Link>
              <button type="button" className="btn btn-ghost" onClick={handleStartOver}>
                Set another mandate
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
