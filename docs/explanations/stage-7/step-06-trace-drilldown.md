# Stage 7 Component 6 — trace drill-down

## 1. What this component does

This is the payoff screen: the place where Sacred Gate 2's abstract promise
("every claim references the tool output that produced it") becomes something
a person can actually click through and check. A new route,
`/study-runs/:studyRunId/traces`, shows every `tool_call_trace` a study run
made, in order, grouped by walk-forward window. Every claim inside
`VerdictCard` (Component 5) is now a real link to the exact trace that
produced it — click it and land on that trace, scrolled into view and
highlighted. Every `HypothesisRow` also carries a plain "View trace" link,
whether or not the hypothesis has a verdict yet, because the traces themselves
are real recorded evidence regardless of how the run turned out.

**Not in scope:** anything that changes data. This is a second, deeper read
surface over data Component 1 already exposed (`GET /study-runs/{id}`,
`GET /study-runs/{id}/traces`, `GET /hypotheses/{id}`) — no backend code
changed. It's also not a general-purpose JSON viewer; the summarization logic
(Section 2) is specifically shaped around what this project's own tools
actually return, not an arbitrary-schema browser.

---

## 2. Every meaningful line explained

### `frontend/src/components/TraceCard.tsx`

```ts
function summarizeScalars(obj: Record<string, unknown>): { scalars: [string, Scalar][]; hasMore: boolean } {
  const scalars: [string, Scalar][] = []
  let hasMore = false
  for (const [key, value] of Object.entries(obj)) {
    if (value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      scalars.push([key, value as Scalar])
    } else {
      hasMore = true
    }
  }
  return { scalars, hasMore }
}
```

One rule, applied identically to any tool's `arguments` or `result`: top-level
primitive values are "worth showing immediately," anything else (an array, a
nested object) isn't. This was written after actually reading real trace rows,
not designed in the abstract — `run_backtest`'s real result mixes seven or so
scalar metrics (`sharpe_ratio`, `num_trades`, `win_rate_pct`, ...) with two
70-plus-entry arrays (`trade_returns`, `exit_bars`) and a list of indicator
names, while `test_significance`'s result is all scalars. The `value as Scalar`
cast exists because TypeScript's control-flow narrowing didn't carry the
`typeof` checks through cleanly for a destructured `for...of` loop variable in
this case — the runtime check just above it is what actually guarantees
correctness; the cast only tells the compiler what the check already proved.

```tsx
function TraceObjectBlock({ label, obj }: { label: string; obj: Record<string, unknown> }) {
  const [showRaw, setShowRaw] = useState(false)
  const { scalars, hasMore } = summarizeScalars(obj)
  ...
  {hasMore && (
    <>
      <button ... onClick={() => setShowRaw((v) => !v)}>{showRaw ? 'Hide full JSON' : 'Show full JSON'}</button>
      {showRaw && <pre className="trace-raw">{JSON.stringify(obj, null, 2)}</pre>}
    </>
  )}
```

Local, unexported component used exactly twice (once for `arguments`, once for
`result`) inside `TraceCard` — small enough that a separate file would be pure
overhead, but named and factored out from `TraceCard`'s own render so the two
nearly-identical blocks don't get typed out separately with a chance to drift.
The toggle defaults to closed: the scalar summary answers "what did this call
actually produce" for the common case, and the full data is one click away,
never permanently hidden.

### `frontend/src/routes/TraceDrilldownPage.tsx`

```tsx
const runRes = await client.GET('/study-runs/{study_run_id}', { params: { path: { study_run_id: studyRunId as string } } })
if (runRes.error || !runRes.data) {
  setLoadError(extractErrorDetail(runRes.error, `No study run with id "${studyRunId}".`))
  return
}
setStudyRun(runRes.data)

const [tracesRes, hypothesisRes] = await Promise.all([
  client.GET('/study-runs/{study_run_id}/traces', ...),
  client.GET('/hypotheses/{hypothesis_id}', { params: { path: { hypothesis_id: runRes.data.hypothesis_id } } }),
])
```

The study run is fetched *first and alone*, not in parallel with the other two
— because both of the other requests need something only the study-run
response provides (`hypothesis_id`), and because a study run that doesn't
exist at all is a wholly different failure (Section 3, case 4) than one that
exists but happens to have zero traces. Once the run is confirmed real, traces
and the owning hypothesis fetch together, since neither depends on the other.

```tsx
const targetTraceId = hash.startsWith(HASH_PREFIX) ? Number(hash.slice(HASH_PREFIX.length)) : null

useEffect(() => {
  if (targetTraceId === null || !traces) return
  const el = document.getElementById(`trace-${targetTraceId}`)
  if (!el) return
  el.scrollIntoView({ behavior: document.hidden ? 'instant' : 'smooth', block: 'center' })
}, [targetTraceId, traces])
```

`useLocation().hash` includes the leading `#`, matching `window.location.hash`
convention, so `HASH_PREFIX = '#trace-'` is stripped by length rather than a
magic `7`. The effect is gated on `traces` (not just `targetTraceId`) because
before the fetch resolves, the page is still rendering "Loading traces…" —
`document.getElementById` would find nothing, and running the effect anyway
would silently do nothing rather than correctly waiting for the DOM the trace
list actually needs. `document.hidden` branch: see Section 3's full account of
why `'smooth'` alone is not a safe default here.

```tsx
{traces.map((trace, i) => (
  <div key={trace.id}>
    {(i === 0 || trace.window_index !== traces[i - 1].window_index) && (
      <div className="card-eyebrow" ...>Window {trace.window_index}</div>
    )}
    <TraceCard trace={trace} highlighted={trace.id === targetTraceId} />
  </div>
))}
```

The window divider compares each trace's `window_index` to the *previous*
trace's, not to some running counter — correct specifically because the
backend already orders traces by `step_index` (Component 1's own router), and
`step_index` order and `window_index` order are guaranteed to agree (a window
finishes before the next one starts in the execution loop). Comparing against
`i === 0` for the very first row avoids an off-by-one where window 0's own
divider would otherwise never render (there is no "previous" trace to differ
from at `i === 0`).

### `frontend/src/components/VerdictCard.tsx` (edit)

```tsx
<Link className="trace-link" to={`/study-runs/${verdict.study_run_id}/traces#trace-${claim.tool_call_trace_id}`}>
  view trace #{claim.tool_call_trace_id}
</Link>
```

Both `verdict.study_run_id` and `claim.tool_call_trace_id` were already on the
objects this component receives — Component 6 needed zero prop changes
upstream of `VerdictCard`, because `VerdictOut`/`Claim` were already shaped for
exactly this link the day they were designed (`src/api/schemas.py`'s own
comment on `Claim`: bound to the tool call that produced it).

### `frontend/src/components/HypothesisRow.tsx` (edit)

```tsx
{hypothesis.study_run_id && (
  <div style={{ marginTop: 6 }}>
    <Link className="trace-link" to={`/study-runs/${hypothesis.study_run_id}/traces`}>View trace</Link>
  </div>
)}
```

Placed as its own line, outside the `card-top` div that owns the
expand/collapse click handler — putting it inside that div would mean a click
on the link also bubbles up and toggles the (unrelated) expand state, needing
an `e.stopPropagation()` workaround. A sibling element sidesteps the problem
entirely rather than papering over it. The condition is `hypothesis.study_run_id`
alone — not `isResolved`, not `verdict` — which is the whole point (Section 3).

---

## 3. Design decisions and rejected alternatives

### The broken-reference case — driven directly by a question asked before any code existed

Before writing this component, the user asked what happens if a claim points
to a `tool_call_trace_id` that can't be found, framing it precisely: this is
the one screen whose entire job is proving the no-fabrication guarantee, so
it's the one place a guessed or silently-dropped reference would be worst.

**Chosen:** four distinct states, worded differently on purpose. (1) Found —
scroll and highlight. (2) The hash names an id, the run's trace list loaded
successfully and isn't empty, but that id isn't in it — a permanent, non-
collapsible callout in the rejected color: *"Trace #{id} was not found among
the traces recorded for this study run."* Nothing else — no guess at which
claim it was, no "closest match," no placeholder trace card standing in for
the real one. The actual, complete trace list for the run still renders below
it, just with nothing highlighted. (3) The run resolved but genuinely has zero
traces — a plain, different-worded empty state, not reused from case 2,
because "nothing here" and "the thing you wanted specifically isn't here" are
different facts. (4) The study run id itself doesn't resolve — the backend's
own real error detail, via the same `extractErrorDetail` pattern Component 4
already established, not a generic "something went wrong."

**Alternative genuinely considered:** add a new `GET /traces/{trace_id}`
lookup-by-id endpoint, so case 2's message could say something more specific
— "this trace was deleted" versus "this trace belongs to a different study
run entirely." **Rejected:** that's backend scope this component doesn't
otherwise need, and more importantly, the honest message doesn't actually
require it — "not found in this run's own list" is exactly, completely true
given what's actually fetched, and inventing a more specific-sounding
diagnosis without a way to confirm which one applies would be a smaller
version of the exact fabrication problem this screen exists to prevent. Said
as a live tradeoff rather than silently decided, since it was a real fork with
a real cost either way. **Cost to reverse:** low — the endpoint could be added
later without touching any of this component's existing rendering logic, only
sharpening the wording of case 2 specifically.

### Verified as a real, seeded database inconsistency — not a hand-typed URL

The user's second instruction, also before any code existed: don't trust this
design by inspection — construct an actual broken reference in the real
database and click through the real rendered UI, the same standard the
`useStudyRunPoll` fix in Component 5 was held to.

**Chosen:** a synthetic hypothesis, `completed` study run, one real trace row,
and a synthetic verdict whose one claim names a `tool_call_trace_id` that
matches neither that trace nor anything else in the table — then clicking the
claim's own rendered link (a dispatched click on the real anchor, not a
hand-typed URL), which exercises `VerdictCard`'s link construction, the real
route transition, and `TraceDrilldownPage`'s fetch-and-match logic together,
in the order a real user would trigger them. **Alternative rejected:** just
navigating directly to a crafted URL with a bogus hash against the destination
page. That would have verified `TraceDrilldownPage` in isolation but not
whether `VerdictCard` actually constructs the link correctly in the first
place — a bug in the link's own template string would have passed that
narrower test while still being broken for a real click. **Cost to reverse:**
none; this was a verification methodology choice, not a code structure one.

### `document.hidden`, not a timer, decides when to skip the smooth-scroll animation

Found during that same live test: `scrollIntoView({ behavior: 'smooth' })`
never actually moved the page at all — `window.scrollY` stayed exactly `0`
across several real seconds — while `behavior: 'instant'`, tested directly in
the same environment, worked on the very next check. This is the identical
root cause behind Component 5's count-up bug: browsers suspend paint-driven
animations, `requestAnimationFrame` and smooth-scrolling alike, for a
document that isn't visible, because there's nothing to paint.

**Chosen:** check `document.hidden` once and pick `'instant'` or `'smooth'`
accordingly, before ever calling `scrollIntoView`. **Alternative considered:**
reuse Component 5's exact pattern — fire the smooth scroll, then a
`setTimeout` backstop that forces an instant scroll if it doesn't look like it
worked. **Rejected specifically because scroll position and a numeric display
value fail differently.** The count-up's failure mode was a *wrong number
staying on screen* — silently incorrect, needing a guaranteed correction. A
missed smooth-scroll's failure mode is *the page not moving at all* while a
real animation might still be legitimately in flight — a timer-based backstop
risks firing while a real smooth scroll on a genuinely visible tab is still
animating, visibly interrupting it and replacing a small polish detail with a
jarring cut. Checking visibility up front has no such race: if the document is
hidden, there is no animation in progress to interrupt (nothing is being
painted regardless), and the position is already correct by the time anyone
actually looks; if it's visible, the smooth animation runs uninterrupted to
completion. **Cost to reverse:** trivial — one conditional.

### A tool-agnostic scalar/JSON split, not per-`tool_name` renderers

Covered in Section 2's walkthrough; the alternative (a `switch` on `tool_name`
with bespoke formatting per tool) was rejected because Stage 4 defines six
tool categories today and nothing stops a seventh from being added later — a
hardcoded switch is a maintenance obligation every new tool inherits silently,
where the generic rule needs nothing changed at all.

---

## 4. Concepts introduced

**Walk-forward windows, made visible.** `window_index` — already a real,
documented field (`agentic_core/db/models.py`'s own comment on
`ToolCallTrace.window_index`: "stamped at write time... Component 7 has to
attribute a claim to the window it came from") — was previously only readable
by querying the database directly. This component is the first place a person
can actually see, in order, which calls belonged to the in-sample window
versus each out-of-sample one, which is the concrete shape of what
"walk-forward testing" produced for a specific hypothesis.

**Paint-suspended animations, generalized.** Component 5 found that
`requestAnimationFrame` doesn't fire for a hidden document. This component
found the *identical* browser behavior governing `Element.scrollIntoView`'s
`'smooth'` behavior — both are driven by the same underlying rendering
pipeline (there's no frame to animate if nothing is being composited to
screen). The lesson generalizes beyond either specific API: any browser
feature described as "animated" or "smooth" is a candidate for this same
failure mode on a backgrounded tab, and needs either a visibility check or a
non-animated correctness guarantee, not just an assumption that the animation
callback will eventually run.

---

## 5. How this component was verified

All against the real backend and real dev Postgres.

**The happy path, on real historical data.** Navigated directly to the real
study run (`94df3b4a-...`, 8 real traces across 4 windows) — confirmed all
traces render in step order with correct window dividers, `run_backtest`'s
real scalar metrics (Sharpe, trade count, win rate, drawdown) visible by
default with `trade_returns`/`exit_bars`/the embedded rule collapsed behind
"Show full JSON" (clicked one open and confirmed the real full JSON renders),
and `test_significance`'s fully-scalar result shown complete with no toggle
needed (matching `summarizeScalars`'s own logic: nothing to hide). Confirmed
the breadcrumb names the real hypothesis and links back to its real charter.

**The deep link, end to end, with cross-checked values.** From the real
rejected hypothesis's `VerdictCard`, clicked the claim referencing
`tool_call_trace_id: 15` — confirmed the resulting URL matched the exact
expected shape, confirmed (via `getBoundingClientRect`) the matching trace
card was both `highlighted` *and* actually inside the viewport (not just
class-tagged), and independently confirmed its rendered `sharpe_ratio` value
(`-1.5099349640345165`) matched the claim's own stated value exactly — proof
the link isn't merely well-formed but actually lands on the trace that backs
the number the claim asserts.

**The broken-reference case, constructed for real.** Seeded a synthetic
hypothesis, `completed` study run, one real trace, and a synthetic verdict
whose claim named a `tool_call_trace_id` matching nothing in the table.
Clicked the claim's own rendered link. Confirmed the exact designed callout
appeared, confirmed the real seeded trace still rendered underneath
un-highlighted (the broken reference didn't suppress or corrupt real data),
and confirmed no part of the page substituted a guess. Separately confirmed
the bogus-study-run-id case reuses the backend's real 404 detail text. All
five synthetic rows deleted afterward, absence reconfirmed by re-query.

**The scroll bug, caught and fixed by this same live testing.** The deep-link
test above initially failed silently — the highlight class was correct but
`window.scrollY` never moved. Diagnosed by testing `'instant'` directly (which
worked), root-caused to the same paint-suspension behavior Component 5's
count-up bug had, fixed, and the deep-link test above was re-run in full from
scratch afterward to confirm the fix rather than assuming it worked from the
diagnosis alone.

**A tooling note, not a code finding.** During this session's own testing,
`computer`-tool coordinate and element-ref clicks intermittently failed to
register on the row's expand toggle in this specific hidden-Browser-pane
environment (`aria-expanded` stayed `false` across two separate attempts with
verified-correct coordinates), while a directly dispatched `el.click()` on the
identical element toggled it correctly — the same DOM event, the same
`onClick` handler, the same code path a real mouse click would take. This was
used for the remainder of this component's click-driven verification once
confirmed equivalent, and is recorded here as a real observation about this
session's own testing environment, not a defect being papered over.

**What this does not prove.** Case 3 (a study run that resolved with zero
recorded traces) was not independently live-seeded and tested this pass — it
shares the same simple, non-animated conditional-render structure as the other
cases and was verified by code reading rather than a fourth synthetic seed.
Nothing about walk-forward window grouping was tested against a run with more
than 4 windows or with windows appearing out of `step_index` order (which
shouldn't be possible given how the execution loop writes traces, but wasn't
independently constructed to confirm).

---

## 6. Interview defense

**"Walk me through what happens if a claim's trace reference is broken."**
Four distinct states depending on exactly what's known: found (scroll,
highlight); not found in a non-empty, successfully-fetched list (a permanent
callout naming the specific missing id, the real trace list still shown
underneath); the run has no traces at all (a different, plainer empty state);
the run itself doesn't exist (the backend's real error). The not-found message
says only what's actually verified — never a guess at cause — and this was
built and tested as a real, seeded database inconsistency clicked through the
real UI, not assumed correct by inspection.

**Hard question: "You found the exact same 'suspended animation' bug twice in
one stage — doesn't that suggest you should have generalized the fix after the
first time instead of hand-rolling a second, different one?"** A fair
challenge, and the honest answer is that the two fixes are *deliberately*
different, not accidentally inconsistent — covered in Section 3. The count-up
needed a guaranteed final *value* regardless of whether animation happened at
all, so a parallel timer that unconditionally lands the correct number is
right there. The scroll needed to *not* interrupt a legitimate animation on a
visible tab, so a visibility check up front is right there instead — a timer
backstop copied verbatim from the count-up fix would have been the wrong tool
for this specific failure mode, not a missed opportunity to share code. What
*should* generalize, and does, is the underlying awareness: this project now
treats "animated/smooth" browser APIs as needing an explicit plan for the
backgrounded-tab case, evaluated per-case rather than papered over with one
reused pattern.

**"Why didn't you just make every trace collapsed by default with a click to
expand, instead of showing scalar metrics inline automatically?"** Because
the scalar metrics — Sharpe ratio, p-value, trade count — are exactly the
numbers a claim references and the reason someone followed a trace link in the
first place; hiding them behind an extra click on every single trace would
make the common case (verify one specific number) slower for no honesty
benefit, since nothing about those values is sensitive or verbose the way the
trade-by-trade arrays are. The one thing genuinely worth collapsing (the large
arrays and the embedded rule tree) is the one thing that's actually collapsed.

**Honest weakness:** the "zero traces recorded" empty state (case 3) was
verified by reading the code, not by constructing a real run with none —
unlike every other state on this page, which was clicked through live. If its
wording or its interaction with the `targetMissing` callout (both could
theoretically render at once, if a hash names an id against an empty list) has
a bug, this pass would not have caught it.

---

## 7. What comes next and why

Component 7 (the scoreboard) is the last piece of `docs/plans/
stage-7-plan.md`'s v1 scope, and it already inherits both backend gaps
Component 5 found (manual `render_verdict`, permanently-`testing` failed
runs) — its own design will need the same honest-state discipline this
component and Component 5 both established, rather than re-deriving it. If
this component's claim-linking were subtly wrong — say, `VerdictCard`
constructing a link with the wrong `study_run_id` — a scoreboard entry linking
back to "why was this confirmed" would land on the wrong run's traces
entirely, and because the link would still *resolve* to some valid page (just
the wrong one), that failure mode would be far harder to notice than an
outright broken link. The cross-checked value verification in Section 5 (the
claim's own value matching the trace's real result) is specifically what rules
that out for the path actually tested.
