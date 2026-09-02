# Stage 7 Component 5 — the research log, with the status-poll reveal

## 1. What this component does

This is the screen that replaces waiting-and-refreshing with actually watching:
under a confirmed charter, every hypothesis the agent has generated, its status,
and — for one still being tested — a live, animated indication that something is
actually happening, followed by the verdict appearing on its own the moment it's
ready. `CharterDetailPage.tsx` (Component 4's "View charter" link already points
here) goes from a stub to a real page that fetches a charter, fetches its
hypotheses, and renders each one through a new `HypothesisRow` component that
owns its own polling and reveal behavior.

**Not in scope:** anything that triggers new work. There is no "generate a
hypothesis" or "start a study" button here, because none exists anywhere in this
API — every hypotheses/study-runs/verdicts route is `GET`-only (confirmed by
reading every router in `src/api/routers/`), and this component is read-only by
the same design `docs/plans/stage-7-plan.md` set for every prior read surface.
Trace-level drill-down (each claim linked to the exact tool call that produced
it) is Component 6's job — this component fetches a verdict's claims and shows
them, but doesn't make them clickable into anything yet.

---

## 2. Every meaningful line explained

### `frontend/src/hooks/useStudyRunPoll.ts`

```ts
const RUNNING_POLL_MS = 5000
const AWAITING_VERDICT_POLL_MS = 20000
```

Two different cadences for two different kinds of waiting. 5 seconds while a
study is genuinely executing (a real run takes roughly 1–3 minutes per Stage
5/6's own measured data, so 5s feels responsive without hammering the backend).
20 seconds once the loop itself has finished but nothing has produced a verdict
yet — see the design-decisions section for why that second state exists at all
and isn't just "still running."

```ts
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
    }
    poll()
    return () => {
      cancelled = true
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current)
    }
  }, [studyRunId, enabled])

  return studyRun
}
```

`setTimeout` chaining a fresh call from inside the previous call's own response
handler, rather than `setInterval` on a fixed cadence, for two concrete reasons.
First, `setInterval` fires on a wall-clock schedule regardless of whether the
previous request has actually returned — if a poll response ever took longer
than 5 seconds (a slow network, a loaded dev machine), `setInterval` would
happily fire a second overlapping request on top of the first, and now two
in-flight responses can race to call `setStudyRun` in either order. Chaining
means there is never more than one request in flight for this row, ever, by
construction. Second, the delay itself needs to *change* (5s while running, 20s
once completed-but-unverdicted) — `setInterval` locks in one interval for its
whole lifetime; a `setTimeout` chain can trivially schedule a different delay
on its very next call. `cancelled` (closed over per-effect-run) guards against
a very specific race: the request that's in flight when the component unmounts,
or when `enabled`/`studyRunId` changes, would otherwise call `setStudyRun` on a
hook instance that no longer wants updates — `cancelled` makes that a no-op
instead of a "set state on an unmounted-in-spirit effect" bug. `timeoutRef`
(rather than a plain local variable) exists specifically so the cleanup function
can reach the *latest* scheduled timeout id — a plain `let timeout` declared
inside `poll()` would go out of scope by the time cleanup runs.

The three-way branch on `data.status`/`data.verdict_id` is the entire "what does
completion actually mean" logic, covered in full in Section 3 — the short version
is that `status === 'completed'` is necessary but not sufficient for "done."

### `frontend/src/components/VerdictCard.tsx`

```tsx
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
  const settle = window.setTimeout(() => setDisplayedThreshold(target), COUNT_UP_MS)
  return () => {
    cancelAnimationFrame(raf)
    window.clearTimeout(settle)
  }
}, [verdict.corrected_significance_threshold])
```

The `requestAnimationFrame` loop is the actual animation — it recomputes a
0-to-1 progress ratio against real elapsed time on every frame the browser is
willing to paint, and sets the interpolated value. The `window.setTimeout`
alongside it is not decoration; it's there because live testing proved rAF
callbacks can simply never fire at all (Section 5 has the exact measurement).
`setTimeout` is a second, independent guarantee that the *correct final value*
lands within `COUNT_UP_MS`, regardless of whether a single animation frame ever
painted. Both are cleaned up on unmount/re-run so a stale card can't keep
writing state after the verdict it belongs to has changed.

### `frontend/src/components/HypothesisRow.tsx`

```tsx
const effectiveStatus = verdict?.status ?? hypothesis.status
const isTesting = effectiveStatus === 'testing'
const isResolved = RESOLVED_STATUSES.includes(effectiveStatus)
```

This replaced a version that read `hypothesis.status` directly. `hypothesis` is
a prop, fetched once by `CharterDetailPage` when the page loads, and never
refetched — so if this specific row resolves while the page is open (the entire
point of live polling), that prop is now lying. `verdict?.status` is the correct
override once it exists, not a guess: `render_verdict` (`agentic_core/verdict.py`)
writes the identical status value to both the `Verdict` row and the `Hypothesis`
row in one operation, so there is no code path where they could disagree — using
`verdict.status` here isn't a heuristic, it's reading the same fact from a
fresher source, because this component has no way to ask the parent to refetch.
Section 5 has the live bug this replaced and how it was actually caught.

```tsx
const studyRun = useStudyRunPoll(hypothesis.study_run_id, isTesting || (isResolved && expanded))
```

One hook, two different reasons to be "on." While `isTesting`, this is genuinely
live polling — watching for the row to resolve. Once `isResolved` and the row is
`expanded`, the *same* hook call becomes a one-shot lookup: `HypothesisOut` never
carries `verdict_id` directly (only `study_run_id` — see `StudyRunOut`'s own
schema comment on why the foreign key points the other way), so reaching an
already-finished hypothesis's verdict still needs one `GET /study-runs/{id}` call
first. Reusing the poll hook for this, rather than writing a second one-off fetch,
works because polling an *already-completed* run just resolves on its first call
and schedules nothing further — the hook doesn't need a special "single-shot"
mode; the general behavior already degenerates to exactly that.

```tsx
useEffect(() => {
  if (!studyRun?.verdict_id || verdict || isLoadingVerdict) return
  ...
  if (data) {
    setVerdict(data)
    if (isTesting) setExpanded(true)
  }
  ...
}, [studyRun?.verdict_id])
```

`if (isTesting) setExpanded(true)` is the entire "reveal" — it only auto-expands
when the verdict arrived *while watching it happen live*. A hypothesis that was
already resolved when the page loaded gets fetched lazily on click and stays
collapsed until then; auto-expanding every historical resolved hypothesis on
page load would bury the one thing worth drawing attention to (a fresh result)
under however many old ones a charter has accumulated.

```tsx
{isTesting && (
  <div className={`trace-card${studyRun?.status === 'completed' ? ' paused' : ''}`}>
    {studyRun?.status === 'failed' ? (...) : studyRun?.status === 'completed' ? (...) : (...)}
  </div>
)}
```

Three visually distinct sub-states inside "still testing," not two. Live/running
gets the full animated trace. `completed`-with-no-verdict gets the same trace
visual but frozen (`.paused`) with different copy — same shape, deliberately
different affect, so "still working" and "done, waiting on a person" don't read
as the same thing at a glance. `failed` gets a wholly different treatment (a
labeled badge, not a trace at all), because unlike the other two, it never
resolves into anything else — see Section 3.

### `frontend/src/routes/CharterDetailPage.tsx`

```tsx
const [charterRes, hypothesesRes] = await Promise.all([
  client.GET('/charters/{charter_id}', ...),
  client.GET('/hypotheses', { params: { query: { charter_id: charterId as string } } }),
])
```

Both requests fire together rather than sequentially (fetch the charter, *then*
fetch its hypotheses) because they don't depend on each other's results — only
on the same `charterId` route param. Sequencing them would just be waiting twice
for no reason.

```tsx
{!charter.confirmed ? (
  <p ...>This charter isn't confirmed yet ... no hypotheses can exist until it is.</p>
) : hypotheses.length === 0 ? (
  <p ...>No hypotheses yet.</p>
) : ( ... )}
```

Two different empty states, not one. `charter.confirmed` is already on the
object this page fetches anyway (Component 2/4's own field), so distinguishing
"nothing here because nothing can exist yet" from "nothing here yet, but it
could" costs nothing extra and is a materially different, more honest message —
matching Component 4's own precedent of using the exact data already in hand
rather than inferring state from its absence.

---

## 3. Design decisions and rejected alternatives

### `render_verdict` is not automatic, and the poll hook has to know that

**Found while reading `agentic_core/loop_graph.py` and `agentic_core/verdict.py`
for this component** (not mentioned anywhere in `docs/plans/stage-7-plan.md`):
`make_finalize` — the node that ends the execution loop — only ever writes
`StudyRun.status`. It never touches the hypothesis row. `render_verdict` (the
function that actually reads the finished run's traces, decides
confirmed/rejected/inconclusive, and writes both the `Verdict` row and the
`Hypothesis.status` update) is a *separate* call, and grepping every call site
in the repo turns up exactly three: `scripts/render_verdict.py`, the eval
harness, and the gate-verification scripts. Nothing wires it to fire
automatically when a loop finishes. So `StudyRun.status == 'completed'` really
only means "the loop stopped," not "there is something to show."

**Chosen:** treat `completed` with no `verdict_id` as a third, distinct polling
state — keep checking, just much less often (20s), and show a visibly different
message ("study finished — verdict not yet rendered") rather than either
silently equating it with `running` or giving up.

**Alternatives considered:** (1) treat `completed` as terminal regardless of
`verdict_id` and stop polling — **rejected** because it would mean a hypothesis
that finishes its loop while nobody happens to be running `render_verdict.py`
by hand would look permanently done-but-empty in the UI, with no way to ever
show its real result short of a manual page reload minutes or hours later, once
someone finally runs the script. That's not "the UI is honest about a gap," it's
"the UI actively hides that a result might still be coming." (2) build a
dedicated "verdict pending" banner with its own separate polling loop —
**rejected** as unnecessary complexity; the existing poll hook already has
everything it needs (`status`, `verdict_id`) to represent this as one more
branch of the same state machine rather than a second one bolted alongside it.

**Cost to reverse:** essentially free once Stage 8 wires `render_verdict` to run
automatically after a loop finishes — the 20s-backoff branch simply stops being
reachable in practice (a poll would almost never land in that window), and
nothing has to be deleted for it to become correct-but-dormant code.

### A failed run's hypothesis is stuck at `'testing'` forever, and gets its own badge instead of "rejected"

**Also found by reading the same two files.** Nothing except `render_verdict`
ever changes a hypothesis's status away from `'testing'`, and `render_verdict`
itself refuses to run against anything but a `completed` run (it raises if
`run.status != 'completed'`). So a `failed` study run's hypothesis has no code
path, today, that ever moves it off `'testing'` — not eventually, not eventually
if someone waits, ever.

**Chosen:** give `failed` its own permanent badge — "Run failed — no verdict
possible", showing `failure_reason` — using the rejected color (red/rose, "bad
outcome") but explicitly *not* the word "rejected."

**Rejected alternative:** reuse the `rejected` pill and label directly, since
the color already communicates "this didn't work out." **Why that's wrong, not
just inconsistent:** `.claude/rules/agent-honesty.md` and the whole verdict
pipeline treat "rejected" as a specific, meaningful claim — the hypothesis *was*
tested and *did* fail its pre-registered falsification condition, with a real
verdict and real claims behind it. A run that crashed before ever reaching that
evaluation has produced no evidence at all. Labeling it "rejected" would be a
small, quiet act of exactly the kind of dishonesty this entire project exists to
prevent — implying a real verdict exists where none does. The color can be
shared (both are bad news); the word cannot.

### The count-up targets `corrected_significance_threshold`, not an arbitrary claim, and starts from 0

`docs/plans/stage-7-plan.md` says "count-up" without specifying what number. A
verdict doesn't have one obvious hero metric the way a single Sharpe ratio
would — it's a narrative plus a *list* of claims, each with its own value.
**Chosen:** `corrected_significance_threshold` — the one field every verdict
always carries, and the concrete, numeric result of the multiple-comparisons
correction (`.claude/rules/agent-honesty.md`'s "track total hypotheses tested...
correct the significance threshold accordingly"). Animating it from 0 up to its
real value, rather than from some conventional baseline like 0.05 down to it,
was a deliberate choice to avoid implying a "before" that was never actually in
play — the threshold was computed fresh from `hypothesis_count_under_charter`
and the grounding tier from the start; there was no moment it was 0.05 and got
corrected downward for *this* hypothesis. Counting up from a fabricated
reference point would be a small, cosmetic version of exactly the honesty
problem this whole system is built to avoid elsewhere.

**Alternative rejected:** count up the first claim's value instead. Rejected
because claims are a list of arbitrary length and the "first" one is an
implementation detail of ordering, not a meaningful choice — there's no reason
claim index 0 deserves the visual emphasis over claim index 4.

### `VerdictCard` is a separate component, on a weaker justification than `CharterSummary`'s

Component 4 extracted `CharterSummary` because it was reused *twice within that
same component* (the reviewing and confirmed states) — real, present-tense
reuse. `VerdictCard` is used exactly once inside `HypothesisRow` today; nothing
in Component 5 itself reuses it. **Chosen anyway**, for two weaker, honestly-
named reasons: it's a large, self-contained rendering concern (narrative, a
claims list, a caveats list, an animated stat) that would make `HypothesisRow`
harder to read if inlined, and Component 6's own stated job — "each claim in a
rendered verdict deep-linkable to the trace that produced it" — is almost
certainly going to extend this exact component rather than write a second one.
That second reason is a bet on near-future reuse, not proven reuse, and it's
named as such here rather than presented with the same confidence as
`CharterSummary`'s justification.

---

## 4. Concepts introduced

**`requestAnimationFrame` and tab visibility.** Browsers throttle or fully
suspend `requestAnimationFrame` callbacks for a document that isn't visible —
there's nothing to paint, so scheduling paint-aligned callbacks is wasted work,
and suspending them is a real, standard power-saving behavior. This was not a
theoretical concern here: a bare rAF loop written purely to test this, waiting
for one accumulated second of frame time, timed out after 45 *real* seconds
without ever completing, while this session's browser pane was hidden — proof
that rAF wasn't merely throttled but not firing at all in that state. The
practical lesson: any UI correctness that depends on "eventually, a frame will
render" needs an independent, not-paint-dependent guarantee (a plain timer) if
it has to be correct even when nobody's looking at the tab — which, for an app
whose own architecture doc says the user "watches progress and can close the
tab," is a completely realistic scenario, not an edge case.

**Derived state vs. stale props.** `HypothesisRow`'s `hypothesis` prop is a
snapshot from whenever the parent last fetched it — accurate at that moment,
increasingly wrong the longer the page stays open and background state changes
underneath it. The general lesson: a value your own component actively knows is
more current than a prop your ancestor handed you should override that prop for
display purposes, not just be ignored in its favor — hence `effectiveStatus`
computed as `verdict?.status ?? hypothesis.status`, falling back to the prop only
when nothing fresher has been learned yet.

**React 18/19 `<StrictMode>` double-invocation.** In development only, React
intentionally mounts, unmounts, and re-mounts components (and re-runs their
effects) to surface code that isn't safe to run twice — a real production
symptom of an *unsafe* double-invoke would be leaked timers, duplicated network
side effects that corrupt state, or a UI stuck in an inconsistent place.
`frontend/src/main.tsx` wraps the whole app in `<StrictMode>` (Vite's own
scaffold default). Observing more `GET /study-runs/{id}` calls than the intended
5s cadence during a single page load is this working as designed, not a bug —
and it's compiled out of `npm run build`'s production output entirely, so it
never reaches a real user. It's still worth understanding: it's evidence the
poll hook's cleanup function is doing its job (no leaked timers survived
multiple mount/unmount cycles to cause visible harm).

---

## 5. How this component was verified

Everything below ran against the real FastAPI backend and real dev Postgres —
no mocked responses — per this stage's established standard.

**Real, pre-existing data.** One real rejected hypothesis with a real verdict
(from actual Stage 5/6 testing) already existed under a real confirmed charter
(`15bc2076-8742-448e-84c3-1bc90087625a`). Navigating there and expanding it
rendered the real prediction, grounding tier, pill, narrative, all 10 real
claims with their real metric/value pairs, and all 5 real caveats — and this is
where the count-up bug was first caught (displayed value stuck at `0.0000`
against a real API response of `0.025`).

**Diagnosing the count-up bug.** Confirmed via `read_network_requests` that the
backend really did return `corrected_significance_threshold: 0.025`. Confirmed
via `document.querySelectorAll('.stat-tile .v')` that the rendered DOM really
did show `"0.0000"`, ruling out a display-formatting mistake. Confirmed the root
cause directly: a standalone script that starts a `requestAnimationFrame` loop
and resolves a promise once 1 second of frame time has accumulated was run in
the same hidden pane, and it **timed out after 45 real seconds** without ever
resolving — hard evidence rAF wasn't firing at all, not just running slowly.
Fixed with the `setTimeout` backstop (Section 2), then re-verified: the count-up
landed on `"0.0250"`, matching the real API value exactly.

**Diagnosing the stale-status bug.** A synthetic `testing` hypothesis and
`running` study run were seeded directly into Postgres (real UUIDs, a real
FK-valid `study_designs` row, a rule JSON that had to be genuinely valid against
`StrategyRule` — the first seed attempt was rejected by the backend with a real
Pydantic error, `"or condition requires at least 2 children"`, confirming the
API's own executability validation was doing its job against my synthetic input,
not a bug in the endpoint) under a real, confirmed, otherwise-empty charter.
Loading the page showed the live "Testing…" trace correctly, and
`read_network_requests` confirmed polling was actually firing. The study run was
then flipped to `completed` with a real `Verdict` row attached, directly in
Postgres, **while the page stayed open** — and the resulting page text showed
the stale "Study finished — verdict not yet rendered" caption sitting directly
above a fully-revealed, correctly-populated verdict card, with no header pill at
all. That inconsistency is what led to finding the stale-prop root cause.
Fixed, rebuilt, re-seeded from scratch, and re-verified: the same live
running→completed transition produced a clean reveal — header pill and the
verdict card's own pill both correctly read `CONFIRMED`, no stale trace-card
text.

**The awaiting-verdict backoff state.** Verified separately: study run flipped
to `completed` with **no** verdict row. Confirmed via `getComputedStyle` on the
live DOM that `.trace-card.paused .trace-path` really does have
`animationName: 'none'` and `opacity: 0.4` (not just that the class name was
present — that the CSS rule actually took visual effect). Confirmed via
`read_network_requests` that this state polls on the slower cadence rather than
continuing at 5s.

**Two honest empty states.** A real confirmed charter with zero hypotheses
showed "No hypotheses yet." A real unconfirmed charter showed the "isn't
confirmed yet" message instead of an indistinguishable blank list.

**Cleanup.** All four synthetic rows (hypothesis, study_design, study_run,
verdict) were deleted afterward and their absence independently re-queried —
same cleanup-and-verify discipline `step-02`'s explainer established, so this
verification pass left no synthetic data sitting in the dev database
indistinguishable from real research.

`npm run build` and `npm run lint` were run clean after every code change, not
just once at the end.

**What this does not prove.** The `failed`-run badge was designed and read
back carefully but never driven live — doing so would mean either forcing a
real budget-exhaustion failure through the actual execution loop or writing a
third synthetic seed, and neither was done this pass. No fresh, real,
end-to-end LLM-driven study was run at all in this component's own
verification; every live check used either genuinely pre-existing real data or
synthetic-but-schema-valid rows written directly to Postgres, a deliberate,
disclosed methodological choice made to verify the *frontend's* reaction to
state changes — which is what this component actually is — without spending
real LLM cost and multiple real minutes re-proving the agent loop itself,
something Stage 5 and Stage 6 already gated. If the real loop's `finalize` or
`render_verdict` behavior were subtly different from what's described in
Section 3, this verification pass would not have caught it — that section's
claims rest on reading the actual source, not on running it end to end.

---

## 6. Interview defense

**"Why does the poll hook need three states instead of the two the plan
described (running vs. done)?"** Because `docs/plans/stage-7-plan.md` was
written assuming `completed` means "verdict ready," and that assumption doesn't
hold in this codebase today — `render_verdict` is a manual step, confirmed by
grepping every call site. Collapsing "loop finished" and "verdict exists" into
one state would mean the UI either lies (says "done" when there's nothing to
show) or never updates once someone eventually runs the script. The third state
is not scope creep; it's the plan's own intent, corrected against what the
backend actually does.

**Hard question: "You spent real effort building a count-up animation, and it
was broken the first time you actually looked at it. Doesn't that suggest you
didn't verify your own work before claiming it was done?"** No — and the honest
answer is more useful than a defensive one: the animation logic itself was, and
is, correct (a clamped progress ratio that mathematically reaches 1 given
enough real time, which is why it self-corrected instantly the moment a delayed
frame *did* fire). What was wrong was an unstated assumption — that
`requestAnimationFrame` would fire at all in the environment doing the
verifying. That's precisely the kind of gap that only shows up under a real,
live check, which is why one was run instead of trusting the code by inspection.
The fix — a plain timer that doesn't depend on painting — is strictly more
correct than the original for exactly the real-world case
(`docs/architecture.md`'s own "she watches progress and can close the tab")
where a user might not be looking at this tab when a reveal happens.

**"Why didn't you just re-fetch the whole hypotheses list from the parent once
a row resolves, instead of adding `effectiveStatus` logic inside the row?"**
That was a real alternative — have `HypothesisRow` call back up to
`CharterDetailPage` to trigger a full refetch on resolution. Rejected because it
would mean re-fetching *every* hypothesis under the charter to fix the display
of *one*, wastefully, and because it introduces a parent-child callback purely
to route information the row already has correctly (`verdict.status`) back up
and then straight back down again. `effectiveStatus` uses the freshest fact this
component already possesses, in place, which is less code and one fewer network
round trip for the same correct result.

**Honest weakness:** the `failed`-run path is designed, read carefully, and
believed correct, but was not exercised live (Section 5) — if `StudyRunOut`'s
`failure_reason` field can ever legitimately be `null` in a way this component's
fallback text doesn't handle gracefully, or if there's some other real shape
mismatch, this pass would not have caught it. Naming that plainly is better than
letting "everything else was live-verified" imply this branch was too.

---

## 7. What comes next and why

Component 6 (trace drill-down) makes each `Claim` in `VerdictCard` — which
already carries a real `tool_call_trace_id` — deep-linkable to the actual
`ToolCallTraceOut` row that produced it, via `GET /study-runs/{id}/traces`
(already built and live-verified in Component 1). If this component's
`effectiveStatus` fix were wrong in some case not covered by this pass's live
tests, Component 6 would inherit a `VerdictCard` being shown for a hypothesis
whose status the rest of the page disagrees with — the trace drill-down would
still be technically correct (it reads the verdict's own claims directly), but
the surrounding page would look inconsistent in exactly the way Section 5's bug
did before it was fixed. Component 7 (the scoreboard) will also read
`Hypothesis.status`/`Verdict.status` directly (the same `ScoreboardEntry` gap
`docs/plans/stage-7-plan.md` already discloses) — the two backend gaps found
here (manual `render_verdict`, permanently-`testing` failed runs) apply there
identically, and whatever scoreboard design responds to them should reuse this
component's handling rather than re-derive it independently.
