import { useEffect, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { client } from '../api/client'
import type { components } from '../api/schema.gen'
import { TraceCard } from '../components/TraceCard'

type StudyRunOut = components['schemas']['StudyRunOut']
type ToolCallTraceOut = components['schemas']['ToolCallTraceOut']
type HypothesisOut = components['schemas']['HypothesisOut']

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

const HASH_PREFIX = '#trace-'

export function TraceDrilldownPage() {
  const { studyRunId } = useParams()
  const { hash } = useLocation()
  const [studyRun, setStudyRun] = useState<StudyRunOut | null>(null)
  const [traces, setTraces] = useState<ToolCallTraceOut[] | null>(null)
  const [hypothesis, setHypothesis] = useState<HypothesisOut | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    if (!studyRunId) return
    let cancelled = false

    async function load() {
      const runRes = await client.GET('/study-runs/{study_run_id}', {
        params: { path: { study_run_id: studyRunId as string } },
      })
      if (cancelled) return
      if (runRes.error || !runRes.data) {
        setLoadError(extractErrorDetail(runRes.error, `No study run with id "${studyRunId}".`))
        return
      }
      setStudyRun(runRes.data)

      const [tracesRes, hypothesisRes] = await Promise.all([
        client.GET('/study-runs/{study_run_id}/traces', {
          params: { path: { study_run_id: studyRunId as string } },
        }),
        client.GET('/hypotheses/{hypothesis_id}', {
          params: { path: { hypothesis_id: runRes.data.hypothesis_id } },
        }),
      ])
      if (cancelled) return
      setTraces(tracesRes.data ?? [])
      setHypothesis(hypothesisRes.data ?? null)
    }

    load()
    return () => {
      cancelled = true
    }
  }, [studyRunId])

  const targetTraceId = hash.startsWith(HASH_PREFIX) ? Number(hash.slice(HASH_PREFIX.length)) : null

  // Client-side route transitions don't trigger the browser's native
  // hash-scroll behavior the way a full page load does -- this does it by
  // hand, once the traces this hash might point at have actually loaded.
  useEffect(() => {
    if (targetTraceId === null || !traces) return
    const el = document.getElementById(`trace-${targetTraceId}`)
    if (!el) return
    // 'smooth' is a paint-driven animation, suspended the same way
    // requestAnimationFrame is for a hidden/backgrounded tab -- the same
    // root cause VerdictCard's count-up fix found first (confirmed live
    // there: a bare rAF loop never fired once in 45 real seconds while
    // hidden). Confirmed here too: 'smooth' left window.scrollY at 0
    // indefinitely on a hidden pane; 'instant' landed immediately. Rather
    // than guess with a timer, just skip the animation outright when the
    // document isn't visible -- the position is already correct by the
    // time anyone actually looks.
    el.scrollIntoView({ behavior: document.hidden ? 'instant' : 'smooth', block: 'center' })
  }, [targetTraceId, traces])

  if (loadError) {
    return (
      <p className="block-body" style={{ color: 'var(--rejected)' }}>
        {loadError}
      </p>
    )
  }
  if (!studyRun || traces === null) {
    return <p className="block-body">Loading traces…</p>
  }

  const targetMissing = targetTraceId !== null && !traces.some((t) => t.id === targetTraceId)

  return (
    <div className="stack">
      {hypothesis && (
        <div className="card-eyebrow">
          <Link to={`/charters/${hypothesis.charter_id}`}>← back to charter</Link>
        </div>
      )}
      <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 28, margin: 0 }}>
        {hypothesis ? hypothesis.prediction : 'Study run traces'}
      </h1>
      <p className="block-body" style={{ color: 'var(--text-3)', fontFamily: 'var(--font-mono)', fontSize: 12, maxWidth: 'none' }}>
        study run {studyRun.id} · {studyRun.status} · {studyRun.step_count} steps
      </p>

      {targetMissing && (
        <div className="card" style={{ borderColor: 'var(--rejected)' }}>
          <p className="block-body" style={{ color: 'var(--rejected)', maxWidth: 'none' }}>
            Trace #{targetTraceId} was not found among the traces recorded for this study run.
          </p>
        </div>
      )}

      {traces.length === 0 ? (
        <p className="block-body" style={{ color: 'var(--text-2)' }}>
          No tool calls have been recorded for this study run{studyRun.status === 'running' ? ' yet' : ''}.
        </p>
      ) : (
        <div className="stack">
          {traces.map((trace, i) => (
            <div key={trace.id}>
              {(i === 0 || trace.window_index !== traces[i - 1].window_index) && (
                <div className="card-eyebrow" style={{ marginTop: i === 0 ? 0 : 18, marginBottom: 8 }}>
                  Window {trace.window_index}
                </div>
              )}
              <TraceCard trace={trace} highlighted={trace.id === targetTraceId} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
