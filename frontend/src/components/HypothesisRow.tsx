import { useEffect, useState } from 'react'
import { client } from '../api/client'
import type { components } from '../api/schema.gen'
import { useStudyRunPoll } from '../hooks/useStudyRunPoll'
import { VerdictCard } from './VerdictCard'

type Hypothesis = components['schemas']['HypothesisOut']

const GROUNDING_LABEL: Record<string, string> = {
  local_corpus: 'grounded — local corpus',
  whitelist_search: 'grounded — whitelist search',
  none: 'ungrounded',
}

const RESOLVED_STATUSES = ['confirmed', 'rejected', 'inconclusive']

interface HypothesisRowProps {
  hypothesis: Hypothesis
}

export function HypothesisRow({ hypothesis }: HypothesisRowProps) {
  const [expanded, setExpanded] = useState(false)
  const [verdict, setVerdict] = useState<components['schemas']['VerdictOut'] | null>(null)
  const [isLoadingVerdict, setIsLoadingVerdict] = useState(false)

  // hypothesis.status is a prop fetched once by the parent and never
  // refreshed -- it goes stale the moment a live poll resolves this row, so
  // it cannot be the sole source of truth for "is this still testing." Once
  // a verdict has actually been fetched (live poll or the one-shot expand
  // lookup below), verdict.status IS the final status: render_verdict
  // (agentic_core/verdict.py) writes the identical value to both the
  // Verdict row and the Hypothesis row in the same operation, so there's no
  // world where they disagree -- using it here just means not waiting on a
  // parent refetch that this component has no way to trigger anyway.
  const effectiveStatus = verdict?.status ?? hypothesis.status
  const isTesting = effectiveStatus === 'testing'
  const isResolved = RESOLVED_STATUSES.includes(effectiveStatus)

  // Two different reasons to watch the study run: live, while testing (to
  // catch the transition), or a one-shot lookup on expand for a hypothesis
  // that was already resolved when the page loaded (its own status is enough
  // to show the collapsed pill, but reaching its verdict needs the study
  // run's verdict_id first -- HypothesisOut doesn't carry that directly, see
  // StudyRunOut's own doc comment on why the FK points the other way).
  const studyRun = useStudyRunPoll(hypothesis.study_run_id, isTesting || (isResolved && expanded))

  // Fires whichever way studyRun.verdict_id became known -- live poll or the
  // one-shot expand lookup above. While isTesting, this IS the reveal: the
  // row auto-expands the moment the verdict exists, rather than making the
  // user click again for something they just watched finish.
  useEffect(() => {
    if (!studyRun?.verdict_id || verdict || isLoadingVerdict) return
    let cancelled = false
    setIsLoadingVerdict(true)
    client.GET('/verdicts/{verdict_id}', { params: { path: { verdict_id: studyRun.verdict_id } } }).then(({ data }) => {
      if (cancelled) return
      setIsLoadingVerdict(false)
      if (data) {
        setVerdict(data)
        if (isTesting) setExpanded(true)
      }
    })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [studyRun?.verdict_id])

  function handleToggleExpand() {
    if (!isResolved) return
    setExpanded((v) => !v)
  }

  return (
    <div className="card">
      <div
        className="card-top"
        role={isResolved ? 'button' : undefined}
        tabIndex={isResolved ? 0 : undefined}
        aria-expanded={isResolved ? expanded : undefined}
        onClick={isResolved ? handleToggleExpand : undefined}
        onKeyDown={
          isResolved
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  handleToggleExpand()
                }
              }
            : undefined
        }
        style={isResolved ? { cursor: 'pointer' } : undefined}
      >
        <div>
          <div className="card-eyebrow">{GROUNDING_LABEL[hypothesis.grounding_tier] ?? hypothesis.grounding_tier}</div>
          <h3 className="card-title" style={{ fontSize: 18 }}>
            {hypothesis.prediction}
          </h3>
        </div>
        {!isTesting && <span className={`pill ${isResolved ? effectiveStatus : 'inconclusive'}`}>{effectiveStatus}</span>}
      </div>

      {isTesting && (
        <div className={`trace-card${studyRun?.status === 'completed' ? ' paused' : ''}`} style={{ marginTop: 4 }}>
          {studyRun?.status === 'failed' ? (
            <>
              <div className="trace-label">
                <span className="pill rejected">Run failed — no verdict possible</span>
              </div>
              <p className="block-body" style={{ color: 'var(--text-2)', maxWidth: 'none' }}>
                {studyRun.failure_reason ?? 'No failure reason recorded.'}
              </p>
            </>
          ) : studyRun?.status === 'completed' ? (
            <>
              <div className="trace-label">Study finished — verdict not yet rendered</div>
              <svg className="trace-svg" viewBox="0 0 300 64">
                <path className="trace-path" d="M0,32 Q75,4 150,32 T300,32" />
              </svg>
            </>
          ) : (
            <>
              <div className="trace-label">Testing…</div>
              <svg className="trace-svg" viewBox="0 0 300 64">
                <path className="trace-path" d="M0,32 Q75,4 150,32 T300,32" />
              </svg>
            </>
          )}
        </div>
      )}

      {expanded && (isLoadingVerdict && !verdict ? (
        <p className="block-body" style={{ color: 'var(--text-3)', marginTop: 12 }}>
          Loading verdict…
        </p>
      ) : (
        verdict && <VerdictCard verdict={verdict} />
      ))}
    </div>
  )
}
