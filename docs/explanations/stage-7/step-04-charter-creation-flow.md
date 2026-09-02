# Stage 7 Component 4 — the charter creation flow

## 1. What this component does

This is the first real screen in the whole product: the form a stranger sits down
at, types a research direction into, and — after seeing what the system understood,
correcting it if it's wrong, and confirming it — hands off to the agent. It replaces
`scripts/set_charter.py`, the terminal script that has stood in for this since Stage
5, and it adds something the CLI never had: an actual in-place correction
round-trip, driven by Component 2's `POST /charters/{id}/correct`, instead of "exit
and re-run the whole script with different wording."

Three states, one page (`frontend/src/routes/MandatePage.tsx`):

- **entry** — a blank mandate textarea.
- **reviewing** — the parsed-and-resolved charter, plus three actions: confirm,
  request a correction (up to twice), or start over.
- **confirmed** — a locked summary, with copy that explicitly draws the line between
  "correction" (what she just used, pre-confirmation) and "redirection"
  (architecture.md Step 7 — questioning or steering an *already-confirmed* charter),
  which does not exist yet.

**Not in scope here:** anything that happens after confirmation besides showing the
confirmed panel. The research log (hypotheses under a charter, verdict cards, the
status-poll reveal) is Components 5–6 — `CharterDetailPage`, which "View charter"
links to, is still the Component 3 stub. No backend code changed in this component;
Component 2 already built and live-verified every endpoint this UI calls.

---

## 2. Every meaningful line explained

### `frontend/src/components/CharterSummary.tsx`

```tsx
interface CharterSummaryProps {
  charter: Charter
  blocked: boolean
}
```

Takes the *nested* `Charter` object (`row.charter` — `parsed` + `resolved_universe`
+ `screening_as_of` + `screening_group_size`), not the outer `CharterOut` database
row. That's deliberate: this component only ever needs to answer "what did the
system understand," never "whose row is this or is it confirmed" — those are the
page's concerns. Passing the narrower type is what makes it impossible for this
component to accidentally reach into `row.id` or `row.confirmed` and grow a second
responsibility.

```tsx
<h2 className="card-title">
  {parsed.universe.sector ?? 'Any sector'}
  {parsed.universe.industry ? ` / ${parsed.universe.industry}` : ''}
</h2>
```

`sector`/`industry` are both `string | null` (an ungrounded mandate can leave either
unset). The `??` only covers `sector`, because an unset sector with a set industry
would be a genuinely confusing state to hide — showing "Any sector / Consumer
Electronics" if that ever happened is more honest than silently dropping the
industry.

```tsx
<div className="v">{screening_group_size}{'→'}{resolved_universe.length}</div>
```

Mirrors `set_charter.py`'s two-sentence disclosure ("N tickers matched... M
survived the cut") as one number pair, because the CLI's own sentence *is* the
`.claude/rules/data-pipeline.md` disclosure requirement in prose form — the
component doesn't invent a new way to say it, it compresses the same two numbers
into the stat-tile idiom `fathom.css` already established for a single quantity
plus a `.sub` caption.

```tsx
{resolved_universe.length > 0 ? resolved_universe.join(', ') : 'none'}
```

An empty resolved universe is exactly the `blocked` condition — this line and the
`blocked` pill above it are two views of the same fact (`resolved_universe.length
=== 0` ⟺ `blocked`), not two independent checks. `blocked` is still passed as a
separate prop rather than computed inside the component, because the backend's own
`create_charter`/`correct_charter` already compute it (`src/agentic_core/charter.py`
— `blocked = len(charter.resolved_universe) == 0`), and re-deriving the identical
boolean client-side from a different field would be exactly the kind of duplicated
logic that drifts if the backend's definition of "blocked" ever changes to mean
something more than "empty universe."

### `frontend/src/routes/MandatePage.tsx`

```tsx
type Phase = 'entry' | 'reviewing' | 'confirmed'
const [phase, setPhase] = useState<Phase>('entry')
const [isCorrecting, setIsCorrecting] = useState(false)
const [isSubmitting, setIsSubmitting] = useState(false)
```

Three top-level phases, not a phase for every network call. `isSubmitting` is one
flag reused across all three POST requests (create/correct/confirm) rather than
three separate loading flags, because at any given moment the page has at most one
request in flight and every render site that needs to know "is a request running"
means the same thing regardless of which request it is — disabling every button and
showing a "…" label. `isCorrecting` is a separate flag, not a fourth phase, because
it toggles a sub-view *within* `reviewing` (the inline correction textarea) rather
than replacing the whole page; folding it into `Phase` would mean every place that
checks `phase === 'reviewing'` to render `CharterSummary` would also need to check
`'correcting'`, duplicating that condition for no behavioral difference.

```tsx
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
```

This function exists because of a real, specific gap between what `openapi-fetch`'s
generated types promise and what the backend actually sends. `src/api/routers/
charters.py`'s `post_correct_charter` and `post_confirm_charter` raise
`HTTPException(status_code=404/409, detail=str(e))` directly — those status codes
are never declared in the FastAPI route's `response_model` or `responses=`, so
`openapi-typescript` never sees them and the generated `error` type for these
operations only covers `422` (`HTTPValidationError`, `detail: ValidationError[]`).
At runtime, a real 404 or 409 still arrives with a real JSON body
(`{"detail": "charter '...' has already used its 2 allowed correction rounds..."}`)
— the type is wrong, not the data. This function reads defensively (`'detail' in
error`, then branches on the *actual* runtime type of `detail`) instead of trusting
the declared type, and falls back to a generic message rather than letting a
`TypeError` reach the user if the shape is ever something else entirely. It is a
narrow, load-bearing exception to "trust the generated types," not a habit — every
other read in this file (`data.charter`, `data.correction_round`, `data.blocked`)
goes through the fully-typed, fully-accurate success path.

```tsx
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
```

All three handlers (`handleSubmitMandate`, `handleSubmitCorrection`,
`handleConfirm`) follow this same shape: set `isSubmitting`, await, clear
`isSubmitting`, branch on `error || !data`, and on success replace `row` wholesale
with the new server response rather than patching individual fields. Replacing the
whole object matters specifically for `handleSubmitCorrection`: `correct_charter`
returns a **new row with a new id** (`agentic_core/charter.py`'s own docstring: "a
correction inserts a NEW charter row"), not an edit to the existing one, so patching
`row.correction_round` in place while keeping the old `row.id` would silently point
every subsequent confirm/correct call at the wrong charter. `setRow(data)` makes
that impossible by construction — there is no code path that keeps a stale `id`
next to a fresh `correction_round`.

```tsx
function handleStartOver() {
  setRow(null)
  setBlocked(false)
  setMandateText('')
  setIsCorrecting(false)
  setCorrectionText('')
  setErrorMessage(null)
  setPhase('entry')
}
```

Resets every piece of state to its initial value and does **not** call any DELETE
endpoint — there isn't one. The charter row created by `create_charter` stays in
Postgres, unconfirmed, orphaned. This is not a gap introduced here: re-running
`set_charter.py` today leaves the exact same kind of orphaned row behind, since the
CLI never deletes anything either. Component 4 reproduces existing behavior rather
than quietly fixing (or quietly worsening) it.

```tsx
{row.correction_round < MAX_CORRECTION_ROUNDS ? (
  <button ...>Request a correction ({MAX_CORRECTION_ROUNDS - row.correction_round} left)</button>
) : (
  <span ...>No corrections remain — confirm as-is or start over.</span>
)}
```

The cap is checked client-side purely for UX (hiding a button the server would
reject anyway) — `correct_charter` enforces the real limit
(`row.correction_round >= MAX_CORRECTION_ROUNDS` → `CorrectionLimitExceededError`,
mapped to a 409 in the router) regardless of what this component believes. If the
two numbers ever disagreed — say, the backend's cap changed and this file wasn't
updated — the failure mode is a wrong button label, not a bypassed limit: the
button would still be clickable, the request would still go to the real backend,
and the real backend would still be the one deciding whether it succeeds.

---

## 3. Design decisions and rejected alternatives

**Chosen: a local component-state machine (`phase` + two booleans), not URL/router
state.** An earlier instinct was to make each phase a route (`/mandate/new`,
`/mandate/:id/review`) so the flow would be bookmarkable and back-button-friendly,
the way `ChartersPage`/`CharterDetailPage` already use route params.
**Rejected:** every transition in this flow is gated by a network response, not by
navigation — there is no legitimate way to *land* on "reviewing" without having just
received a `CharterWriteOut` from the server, and an unconfirmed, mid-correction
charter is not a resource a stranger should be able to deep-link into or refresh
into a stale view of. Route state would also require re-fetching the charter by id
on every mount to reconstruct `blocked`/`correction_round`, adding a network
round-trip this flow doesn't otherwise need. **Cost to reverse:** low — nothing else
in the app currently links into mid-flow state, so adding routes later, if a real
need showed up (e.g., resuming a correction after a page reload), would be additive.

**Chosen: `extractErrorDetail` with runtime type narrowing, not tightening the
backend's OpenAPI schema instead.** The cleaner fix for the type gap above would be
adding explicit `responses={404: ..., 409: ...}` metadata to the three charter
routes in `src/api/routers/charters.py`, which would make `openapi-typescript`
generate accurate error types and let this file drop the `unknown`/`as` casts
entirely. **Rejected for this component specifically:** that's a backend schema
change, and Component 4's own scope boundary (stated in `docs/plans/
stage-7-plan.md`'s build order) is frontend-only — Component 2 already shipped and
live-verified the routes as they are. Reopening router code to satisfy a frontend
typing preference would blur that boundary for a purely cosmetic win (the runtime
behavior is identical either way). **Cost to reverse:** low, and disclosed rather
than hidden — worth doing whenever a router file is touched again for a substantive
reason, not on its own.

**Chosen: duplicate `MAX_CORRECTION_ROUNDS = 2` as a frontend constant, not add an
endpoint to expose it.** The alternative — a `GET /config` route, or adding a
`max_correction_rounds` field to every `CharterOut` — would remove the duplication
entirely. **Rejected:** it's one integer, read in exactly one file, and the
project's own `docs/architecture.md` §8 cost-discipline section already argues
against building infrastructure ahead of a proven need; a config endpoint for a
single constant that has changed zero times since it was introduced is exactly that.
The duplication is named in a comment that points at the real source
(`agentic_core.charter.MAX_CORRECTION_ROUNDS`), so a future change to the backend
constant has one obvious place to also update, rather than being a silent trap.

**Chosen: the confirmed-state panel explicitly names both "correction" and
"redirection" and states that the second doesn't exist yet.** The simpler option was
to say nothing — just show a locked charter with a "Confirmed" pill, since nothing
*requires* the app to explain what it can't do yet. **Rejected, directly per this
session's own instruction:** the two mechanisms (a pre-confirmation LLM re-parse vs.
a hypothetical post-confirmation chat/steering feature) sound similar enough from a
user's seat that leaving the boundary implicit invites someone to go looking for a
"correct" button on a confirmed charter and conclude the app is broken when they
don't find one. Naming the gap costs one paragraph and removes that confusion
outright.

**Chosen: correct two pre-existing phrasing spots (`src/agentic_core/charter.py`'s
`MAX_CORRECTION_ROUNDS` comment, and this document's own sibling explainer,
`step-02-charter-confirm-correct.md`'s interview Q&A) before writing any Component 4
code.** Both previously described a correction as "the same [call/moment]
`parse_charter` already uses, invoked again" without qualifying what that means.
Read in isolation, that phrasing supports a wrong mental model: that a correction
replays an identical request and gets a different answer only by luck. **Rejected
for the reason the user gave directly:** what actually makes re-running
`parse_charter` safe on a correction round is not that the call repeats unchanged —
the prompt is different every time (`_combined_mandate_for_correction`: original
text + restated interpretation + correction) — it's that *every* round, regardless
of what text goes in, passes through the same schema-validated `parse_charter` →
`resolve_universe` pipeline. That distinction is not pedantic: it is the same
"model proposes, code disposes" principle (`.claude/rules/agent-honesty.md`) that
makes the whole agent's fabrication guarantee work in Stage 5 — the safety comes
from validation being applied uniformly, never from the LLM being asked the same
question twice. Getting that phrasing precise here, at small scale, is the same
discipline the sacred-gate claims later depend on being precise at large scale.
**Cost to reverse:** none — this was a documentation-only fix; no code behavior
changed.

---

## 4. Concepts introduced

**Generated-client type coverage vs. actual runtime behavior.** `openapi-fetch` +
`openapi-typescript` type a response based on what the backend's OpenAPI schema
*declares*, not on what the backend can actually send. FastAPI auto-declares
`422` for any route with a Pydantic request body, but a hand-raised
`HTTPException(status_code=404, ...)` inside a route function is invisible to schema
generation unless the route is explicitly annotated with `responses={404: ...}`.
The practical consequence: a generated client can be fully accurate for the "happy
path" and silently wrong for error paths, and the failure mode isn't a compile
error — it's a value at runtime whose real shape doesn't match its declared
TypeScript type. `extractErrorDetail` exists because of exactly this gap.

**Optimistic vs. replace-on-success state updates.** This page never mutates `row`
in place (`row.correction_round++`, `row.charter = newCharter`) — every successful
response fully replaces `row` with the object the server returned. The alternative,
patching known fields optimistically before the response arrives, is a real pattern
elsewhere (it makes UIs feel instant), but it's wrong here specifically because a
correction changes the row's *identity* (a new `id`), not just its fields — there is
no safe partial patch that would end up pointing at the right resource.

---

## 5. How this component was verified

Per `docs/plans/stage-7-plan.md`'s own standard for the frontend ("manual
verification in the browser preview against the real running FastAPI backend — no
mocked backend"), this was checked live, not with a test double:

- `npm run build` (`tsc -b && vite build`) and `npm run lint` (`oxlint`) both clean.
- Real backend (`uvicorn`) + real Postgres + real LLM, driven through the actual
  browser preview:
  - Mandate "momentum on large-cap consumer electronics" → parsed to
    `Technology/Consumer Electronics`, `1→1`, resolved universe `[AAPL]`, round 0 of
    2 — matching `step-02`'s own recorded example exactly.
  - Correction "widen it to all of Technology, not just Consumer Electronics" → a
    **new** charter id, round 1 of 2, `9→2`, resolved universe `[NVDA, MSFT]` —
    again matching `step-02`'s live-verified result. Confirmed via
    `read_network_requests` that this was a distinct `POST .../correct` call
    returning a distinct id, not a client-side relabeling.
  - Confirm → `POST .../confirm` returned `200` with `confirmed: true`; the locked
    panel rendered with the boundary-clarification copy and a "View charter" link
    whose `href` matched the confirmed charter's real id.
  - "Set another mandate" reset the page to a blank entry form.
  - Repeated the visual check in light mode (the app's theme toggle) — form
    controls and buttons render with correct contrast in both themes, using only
    existing design tokens.

**What this does not prove.** The correction-cap-exhausted state (requesting a third
correction after two are used) and the blocked state (an empty resolved universe)
were verified by code inspection and the conditional-rendering logic, not by driving
a real LLM to those specific outcomes live — doing so would require either finding a
mandate that reliably resolves to an empty universe or spending two more real
correction rounds against paid API calls purely to exercise a `<span>` instead of a
`<button>`. The 404/409 error-message rendering (`extractErrorDetail`'s non-happy
paths) is similarly unexercised live, since normal UI flow can't reach a
not-found/already-confirmed/limit-exceeded charter id — those branches exist as
defense-in-depth against a state race (e.g., two tabs open on the same charter),
not a path this UI's own buttons can trigger. None of this touches Sacred Gate 1 or
2; this component has no backtesting or agent-reasoning code in it at all.

---

## 6. Interview defense

**"Why not use URL/router state for the flow instead of local component state?"**
Because nothing in this flow is a resource a stranger should be able to deep-link
into mid-transaction — every phase transition is gated by a fresh server response,
not by navigation, and an unconfirmed charter mid-correction isn't something a
refresh should be able to resurrect into a stale view. See Section 3 for the full
reasoning and what it would cost to add later if a real need showed up.

**Hard question: "Your `extractErrorDetail` function uses `as { detail: unknown }`
— isn't that exactly the kind of type-unsafety Component 3's whole pitch for a
generated API client was supposed to eliminate?"** Yes, for this one narrow case,
and it's worth answering that directly rather than deflecting. The generated client
is fully accurate for every *declared* response shape — every field read off a
successful `data` object in this file is real, checked, generated-from-the-real-
backend typing, with zero hand-maintained duplication. The gap is specifically that
FastAPI's schema generation doesn't see hand-raised `HTTPException`s that aren't
declared with `responses=`, so the *error* path for three specific operations has
an incomplete declared type. The honest fix is backend-side (annotate those routes),
which was deliberately left out of this component's scope (Section 3). What this
function does is fail safely inside that known gap — narrow the `unknown`, check
real runtime shapes, and fall back to a generic message — rather than pretend the
gap doesn't exist by trusting a type that's wrong for 404/409.

**"Why didn't you just let the correction button stay visible and rely on the
backend's 409 to stop a third correction, instead of hiding it client-side at
`correction_round === 2`?"** Because a 409 the user has to trigger by clicking a
button that was always going to fail is worse UX for zero safety benefit — the
server-side check is the actual enforcement either way (Section 3's point about the
client-side check being cosmetic). Hiding the button when it's certain to fail, and
using the freed space to say *why* ("no corrections remain — confirm as-is or start
over"), teaches the two-round policy instead of just blocking a click silently or
surfacing a raw error string for an entirely predictable state.

**Honest weakness:** the blocked-universe and cap-exhausted paths are inspected, not
live-driven (Section 5) — if there were a subtle bug specific to *that* combination
of state (for instance, `blocked` still true after a correction that also happened
to exhaust the cap), this component's live verification would not have caught it.
The mitigation is that both are simple, independent boolean-gated renders reusing
logic already exercised in the round-0-to-round-1 transition, not a new interaction
between them — but that's a code-review argument, not a live-test one, and it's
worth saying so plainly rather than claiming a verification pass this component
didn't actually perform.

---

## 7. What comes next and why

Component 5 (the research log, with the status-poll reveal) reads and displays
hypotheses under a confirmed charter, reached through the "View charter" link this
component produces (`/charters/{id}`, currently `CharterDetailPage`'s stub). If
`confirm_charter` or this component's handling of it were subtly wrong — say, `row`
were replaced with stale data so the UI *believes* a charter is confirmed when the
database still has `confirmed: false` — Component 5 would show a research log with
nothing in it (no hypothesis generation runs against an unconfirmed charter,
Component 5's own eventual backend work will assume `confirmed: true` the way
Stage 5's execution loop already does), and the failure would look like "no
hypotheses were ever proposed" rather than pointing back at this component. The live
verification in Section 5 (an independent read confirming `confirmed: true` after
the confirm call) is what rules that out for the case actually tested; Component 5
inherits the charter id this component hands it and trusts it's real from here on.
