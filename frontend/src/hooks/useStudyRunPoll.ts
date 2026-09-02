import { useEffect, useRef, useState } from 'react'
import { client } from '../api/client'
import type { components } from '../api/schema.gen'

type StudyRunOut = components['schemas']['StudyRunOut']

const RUNNING_POLL_MS = 5000

// A study run can finish (StudyRun.status='completed') well before its verdict
// exists -- render_verdict (agentic_core/verdict.py) is not called automatically
// when the loop finishes (confirmed by grepping every call site: only
// scripts/render_verdict.py, the eval harness, and the gate-verification scripts
// call it). Once we're in that window there's no urgency -- back off hard rather
// than polling a completed row every 5s waiting on a human to run a script.
const AWAITING_VERDICT_POLL_MS = 20000

/**
 * Polls GET /study-runs/{id} for exactly one row. Self-contained: starts a
 * timeout chain when `enabled` and `studyRunId` are both set, clears itself on
 * unmount or once `enabled` goes false. Used two ways by HypothesisRow: live
 * (while a hypothesis is 'testing', watching for it to resolve) and as a
 * one-shot lookup (fetch a resolved hypothesis's own study run once, on
 * expand, to learn its verdict_id -- the first response already has
 * status !== 'running', so the chain naturally stops after one call).
 *
 * Uses a setTimeout chain rather than setInterval so a slow response can't
 * cause overlapping in-flight requests, and so the delay itself can change
 * (5s while running, 20s once completed-but-unverdicted) without restarting
 * anything.
 */
export function useStudyRunPoll(studyRunId: string | null, enabled: boolean): StudyRunOut | null {
  const [studyRun, setStudyRun] = useState<StudyRunOut | null>(null)
  const timeoutRef = useRef<number | null>(null)

  useEffect(() => {
    if (!studyRunId || !enabled) return

    let cancelled = false

    async function poll() {
      const { data } = await client.GET('/study-runs/{study_run_id}', {
        params: { path: { study_run_id: studyRunId as string } },
      })
      if (cancelled || !data) return
      setStudyRun(data)

      if (data.status === 'running') {
        timeoutRef.current = window.setTimeout(poll, RUNNING_POLL_MS)
      } else if (data.status === 'completed' && !data.verdict_id) {
        timeoutRef.current = window.setTimeout(poll, AWAITING_VERDICT_POLL_MS)
      }
      // Otherwise resolved (verdict_id present) or failed -- stop polling.
    }

    poll()

    return () => {
      cancelled = true
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current)
    }
  }, [studyRunId, enabled])

  return studyRun
}
