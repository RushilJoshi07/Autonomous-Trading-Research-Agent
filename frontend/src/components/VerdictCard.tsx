import { useEffect, useState } from 'react'
import type { components } from '../api/schema.gen'

type Verdict = components['schemas']['VerdictOut']

interface VerdictCardProps {
  verdict: Verdict
}

const COUNT_UP_MS = 700

/**
 * Narrative, claims, caveats, and a count-up on corrected_significance_threshold
 * -- the one number every verdict always carries that's actually meaningful to
 * animate (the multiple-comparisons correction, .claude/rules/agent-honesty.md).
 * A verdict doesn't have a single hero metric the way a Sharpe ratio would, so
 * this reveals from 0 rather than implying a specific "before" value that was
 * never really in play.
 */
export function VerdictCard({ verdict }: VerdictCardProps) {
  const [displayedThreshold, setDisplayedThreshold] = useState(0)

  useEffect(() => {
    const target = verdict.corrected_significance_threshold
    const start = performance.now()
    let raf: number

    function step(now: number) {
      const progress = Math.min((now - start) / COUNT_UP_MS, 1)
      setDisplayedThreshold(target * progress)
      if (progress < 1) raf = requestAnimationFrame(step)
    }

    raf = requestAnimationFrame(step)
    // Browsers commonly suspend requestAnimationFrame entirely for a
    // hidden/backgrounded tab (confirmed live: a bare rAF loop never
    // accumulated even one second of frames across 45 real seconds while
    // this pane was hidden) -- rather than let correctness depend on a
    // frame ever actually firing, a plain timeout guarantees the true
    // final value lands within COUNT_UP_MS regardless. Timers are still
    // throttled in the background, just not suspended the way rAF is, so
    // this fires reliably even if every intermediate frame was skipped.
    const settle = window.setTimeout(() => setDisplayedThreshold(target), COUNT_UP_MS)
    return () => {
      cancelAnimationFrame(raf)
      window.clearTimeout(settle)
    }
  }, [verdict.corrected_significance_threshold])

  return (
    <div className="card" style={{ marginTop: 12 }}>
      <div className="card-top">
        <span className={`pill ${verdict.status}`}>{verdict.status}</span>
      </div>

      <p className="card-narrative">{verdict.narrative}</p>

      <div className="stat-row" style={{ marginTop: 18, gridTemplateColumns: 'repeat(2, 1fr)' }}>
        <div className="stat-tile">
          <div className="k">Corrected significance threshold</div>
          <div className="v">{displayedThreshold.toFixed(4)}</div>
          <div className="sub">hypothesis {verdict.hypothesis_count_under_charter} under this charter</div>
        </div>
        <div className="stat-tile">
          <div className="k">Claims</div>
          <div className="v">{verdict.claims.length}</div>
          <div className="sub">each referencing a real tool call</div>
        </div>
      </div>

      {verdict.claims.length > 0 && (
        <div className="stack" style={{ marginTop: 18, gap: 10 }}>
          <div className="card-eyebrow">Claims</div>
          {verdict.claims.map((claim) => (
            <p
              key={`${claim.tool_call_trace_id}-${claim.metric}`}
              className="block-body"
              style={{ color: 'var(--text-2)', maxWidth: 'none' }}
            >
              {claim.statement}{' '}
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-3)', fontSize: 12 }}>
                ({claim.metric} = {claim.value})
              </span>
            </p>
          ))}
        </div>
      )}

      {verdict.caveats.length > 0 && (
        <div className="stack" style={{ marginTop: 18, gap: 6 }}>
          <div className="card-eyebrow">Caveats</div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {verdict.caveats.map((caveat, i) => (
              <li key={i} className="block-body" style={{ color: 'var(--text-2)', maxWidth: 'none' }}>
                {caveat}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
