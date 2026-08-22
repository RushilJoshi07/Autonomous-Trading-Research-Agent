# Stage 5, Component 2 — The charter

## 1. What this component does

This component is the human-in-the-loop step that starts every research
program: she types a mandate in plain English, and this component turns it
into a confirmed, persisted `Charter` row — a real ticker universe, a
closed set of hypothesis families to investigate, a timeframe, and a
scoring preference — that Component 6's eventual agent loop will read as
its starting state. It runs entirely before that loop exists to invoke
anything; per `docs/architecture.md` Step 1, her confirmation is "what
allows the agent to start," so this component has to work correctly and
be trustworthy on its own, standing alone.

**What it is not.** No FastAPI endpoint, no React form — Stage 7 owns
those. No LLM call happens more than once per charter (one call to turn
her sentence into structure; everything after that is deterministic code).
No hypothesis gets generated, no RAG grounding happens (Component 3's
tiered grounding is scoped to Step 2 of the user journey, hypothesis
generation, not Step 1) — Component 2 has zero dependency on Component 3,
and this component's own testing confirmed that independence rather than
just assuming it from the plan.

## 2. Every meaningful line explained

### `agentic_core/schemas.py`

```python
class EffectFamily(str, Enum):
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    LOW_VOLATILITY = "low_volatility"
    VALUE = "value"
    QUALITY = "quality"
    SEASONALITY = "seasonality"
```

Six values, taken directly from `docs/architecture.md`'s own list ("only a
few dozen effect families... momentum, mean-reversion, low-volatility,
value, quality, seasonality"). This is deliberately the *same* enum
Component 3's corpus tagging will use to categorize each of the 30–50
curated papers — not a charter-specific copy. If Component 3 invented its
own separate vocabulary, a hypothesis tagged `"mean_reversion"` and a paper
tagged `"reversal"` would silently refer to the same concept under two
different strings, and nothing would catch the drift; sharing one `Enum`
between the two components makes that kind of drift a Python `ImportError`
or a Pydantic validation failure instead of a silent mismatch.

```python
class UniverseFilter(BaseModel):
    sector: str | None = None
    industry: str | None = None
    metric: Literal["liquidity", "volatility"] = "liquidity"
    cut: Literal["quintile", "tercile", "decile"] = "quintile"
```

`cut` is the field worth pausing on. The first instinct — a raw
`percentile_cut: float` — was rejected before any code was written for it.
`.claude/rules/data-pipeline.md` requires that thresholds be relative and
never hand-picked, specifically because "a hand-picked number can be
quietly retuned until the backtest looks good." At charter-creation time
there is no backtest result yet to retune *against* — the risk that rule
is actually guarding against doesn't technically exist at this exact
moment. The decision made here was to apply the discipline uniformly
anyway, rather than carve out a "this specific moment is safe" exception:
a codebase where relative-threshold discipline holds everywhere except one
place that seemed safe when it was written is a codebase where a future
change can quietly reintroduce the exact risk the rule exists to prevent,
without anyone noticing the exception was ever load-bearing. `cut` being a
closed `Literal` means the only three percentile values that can ever
exist anywhere in this system are the three the architecture document
itself already named.

```python
class ParsedCharter(BaseModel):
    universe: UniverseFilter
    hypothesis_families: list[EffectFamily] = Field(min_length=1)
    timeframe: Literal["daily", "weekly", "monthly"] = "daily"
    history_start: date | None = None
    scoring_preference: Literal["robustness", "raw_returns", "balanced"] = "balanced"


class Charter(BaseModel):
    parsed: ParsedCharter
    resolved_universe: list[str]
    screening_as_of: date
    screening_group_size: int
```

Two models, not one, and this split is the component's central structural
decision. `ParsedCharter` is passed as `response_model` to
`llm_client.structured_output` — it is *exactly* what the LLM is asked to
produce, and nothing about a ticker symbol appears anywhere in its field
list. `Charter` wraps it with `resolved_universe`, which only ever gets
populated by `resolve_universe()`, a function that never calls the LLM at
all. The guarantee this produces is structural, not conventional: there is
no field on `ParsedCharter` a hallucinated ticker could land in, because
the type the LLM is constrained to producing simply has no such field.
Contrast this with the alternative of one merged model where
`resolved_universe` sits next to `universe` as sibling fields — nothing
would stop a future change from accidentally asking the LLM to fill in
*both*, and the guarantee would then depend on every future reader
remembering never to trust the LLM-filled version of that field. The two-
model split makes that mistake a type error instead of a discipline
someone has to remember.

### `agentic_core/charter.py`

```python
CUT_TO_PERCENTILE = {"quintile": 80.0, "tercile": 100.0 / 3.0 * 2, "decile": 90.0}
```

This dictionary is the *only* place in the entire system where a cut name
becomes a number. Before writing it, `src/data_pipeline/screener.py` and
its MCP wrapper in `src/mcp_tools/server.py` were read directly rather than
assumed — and this changed the design. `screen_universe` does not take a
quintile/tercile/decile parameter at all; it ranks *every* ticker matching
`sector`/`industry` and returns each one with a `percentile` field (0–100,
computed relative to the matched group). The caller decides where to cut.
Had this not been checked first, the natural (wrong) assumption would have
been to design `UniverseFilter.cut` as something passed *into*
`screen_universe`'s own parameters — which the tool simply has no slot
for. Discovering the real shape first is what made the actual design
clean: `resolve_universe()` calls `screen_universe`'s underlying `screen()`
once, gets back every candidate's percentile, and applies
`CUT_TO_PERCENTILE[parsed.universe.cut]` as a plain `>=` filter over an
already-fetched list — no second query needed per cut, which is also
exactly what will make Component 7's later sensitivity check ("does this
survive quintile *and* tercile *and* decile") cheap: the same single
screening call's result can be re-cut at all three levels without
re-querying the database.

```python
def resolve_universe(parsed: ParsedCharter, as_of: date | None = None) -> Charter:
    ...
    with SessionFactory() as session:
        result = screen(session, sector=..., industry=..., metric=..., as_of=as_of)
    threshold = CUT_TO_PERCENTILE[parsed.universe.cut]
    tickers = [c.ticker for c in result.candidates if c.percentile >= threshold]
```

`screen()` — the plain function in `data_pipeline/screener.py` — is called
directly, in-process, not through a real MCP client connection to
`mcp_tools.server`. This was a real decision, not an oversight, and it's
worth being precise about why it's correct here specifically. MCP's tool-
registration boundary exists to discipline something specific: an LLM's
*choice* of which tool to call next, with which arguments — that's the
entire point of wrapping tools as MCP servers in Stage 4, and it's exactly
what Component 6's `decide_next_action` loop will need, because in that
loop an LLM genuinely is choosing. Here, nothing is choosing anything —
`resolve_universe` calls the screener every single time,
unconditionally, with arguments already fully determined by
`parsed.universe`. Routing that through a subprocess and a real MCP
client would add real latency to a step that should feel instant to
someone sitting at a terminal typing a mandate, in service of a discipline
that doesn't apply because there's no choice being disciplined. The same
underlying tool logic (`screen()`) gets exercised either way — what
differs is only the transport, and the transport's whole reason to exist
is tied to a decision this component never makes.

```python
def create_charter(mandate_text: str) -> tuple[str, Charter, bool]:
    parsed = parse_charter(mandate_text)
    charter = resolve_universe(parsed)
    blocked = len(charter.resolved_universe) == 0
    ...
    return charter_id, charter, blocked
```

`blocked` is computed from `len(charter.resolved_universe) == 0`, not from
`screening_group_size == 0`. These are different conditions: `group_size`
counts everything matching sector/industry *before* the percentile cut is
applied, while `resolved_universe` is what survives the cut. Using
`resolved_universe`'s length is correct because it's the actual thing
Component 6 needs to be non-empty to do any useful work — a charter could
plausibly have `group_size=1` and still resolve fine (a lone matching
ticker sits at percentile 100 by construction, per `screener.py`'s own
handling of a single-candidate group, and therefore clears any cut), so
`group_size` alone isn't the right blocking signal.

### `scripts/set_charter.py`

The confirmation prompt (`input("\nConfirm this charter? [y/N] ")`) is
only reached when `blocked` is `False`. When it's `True`, the script prints
the diagnostic and calls `sys.exit(1)` before that line is ever reached —
there is no path by which a charter with an empty resolved universe can be
confirmed through this script, because the code that would confirm it is
structurally unreachable in that case, not merely discouraged by a warning
message someone could click past.

## 3. Design decisions and rejected alternatives

### The real accidental discovery: grounding individual fields is not grounding valid combinations of fields

This is the component's most important finding, and it came from testing
requested specifically to see the defense mechanisms fire for real, not
from planning. The first version of `parse_charter`'s prompt queried two
*independent* `DISTINCT` lists from `TickerMetadata` — every real sector
value, every real industry value — and instructed the LLM not to invent a
value absent from either list. Three real mandates were run against it
(three actual `llm_client.structured_output` calls to Bedrock, not
mocked):

"Investigate momentum and mean-reversion strategies on biotech and
pharmaceutical companies, using the most liquid decile" correctly resolved
to `sector='Healthcare', industry='Drug Manufacturers - General'` — both
real values, `group_size=5`, the decile cut kept exactly one ticker
(`LLY`). It did not invent "Biotechnology" or "Pharmaceuticals," neither of
which exists in `TickerMetadata`. "Investigate seasonality effects on
cryptocurrency and blockchain companies" correctly left
`sector=None, industry=None` — no real value applies at all — and fell
back to the full 54-ticker universe rather than inventing something
plausible-sounding. Both of these are the grounding defense working
exactly as intended.

The third mandate, "Investigate low-volatility strategies on consumer tech
companies," is where it broke, in a genuinely instructive way. It produced
`sector='Consumer Cyclical', industry='Consumer Electronics'`. Querying
`TickerMetadata` directly confirmed both values are individually real —
`Consumer Cyclical` is a real sector (six tickers: `NKE`, `CROX`, `HD`,
`AMZN`, `MCD`, `BROS`), and `Consumer Electronics` is a real industry — but
`Consumer Electronics` only ever occurs under `sector='Technology'` in the
actual data (it's `AAPL`'s row, and only `AAPL`'s). No ticker has that
exact combination. `screen()` correctly returned `group_size=0`, and
`create_charter`'s `blocked` flag correctly came back `True`. The real
`scripts/set_charter.py` script was run against this exact mandate through
piped stdin and printed the genuine block message — `"Screening as of
2026-08-22: 0 tickers matched sector/industry, 0 survived the quintile
cut... BLOCKED: the resolved universe is empty"` — exit code 1, the
confirmation prompt never reached.

The root cause, stated precisely: sector and industry are **not
independent** in the real data — every industry belongs to exactly one
sector — but presenting them as two separately-choosable flat lists gave
the model no way to know that. It combined two individually-grounded,
individually-real values into a pairing that has never existed on any
actual ticker. This is a sharper failure than a simple hallucinated string
would have been, and it's worth being explicit about why: grounding a
*field* against real values (does `"Consumer Electronics"` appear anywhere
in the industry column?) is a strictly weaker guarantee than grounding a
*combination* of fields (does `sector='Consumer Cyclical'` co-occur with
`industry='Consumer Electronics'` on any row?). A well-intentioned, mostly-
correct layer-1 defense — the sector/industry grounding — still had a real
gap, and layer 2 — the code-level zero-match block — caught the actual
consequence anyway. That's not a generic "defense in depth is good"
platitude; it's what defense in depth is *for*, demonstrated concretely by
one layer's specific, real gap and the other layer's specific, real catch.

**The fix**, applied after this was found rather than designed in from the
start: `_real_sector_industry_pairs(session)` now runs a single query —
`SELECT DISTINCT sector, industry FROM ticker_metadata WHERE sector IS NOT
NULL AND industry IS NOT NULL` — and groups the results by sector before
they ever reach the prompt:

```
Technology: Communication Equipment, Consumer Electronics, Semiconductors, ...
Healthcare: Diagnostics & Research, Drug Manufacturers - General, Healthcare Plans
```

with an explicit instruction that an industry listed under one sector
never occurs under any other sector in this grouping. The *identical*
"consumer tech companies" mandate, re-run against this fixed prompt (a
fourth real Bedrock call), produced `sector='Technology',
industry='Consumer Electronics'` — a real, actually co-occurring pair —
with `group_size=1`, `resolved_universe=['AAPL']`, `blocked=False`. (The
model also picked `metric='volatility'` this time rather than the default
`'liquidity'`, plausibly reading "low-volatility strategies" as a signal
about the screening metric too, not just the strategy family — noted here
as ordinary LLM interpretation variance, not a defect, since `metric`'s own
`Literal` type fully constrains it to a valid choice regardless of which
one it picks.) The exact mismatch this component found is now structurally
harder to propose in the first place, not merely caught after the fact —
though it's worth being honest that "harder to propose" is not the same
guarantee as "structurally impossible": the zero-match block in layer 2 is
still the property that actually guarantees no confirmed charter can ever
have an empty universe, and layer 1's improvement reduces how often layer
2 needs to fire, rather than replacing the need for it.

### The reusable principle: who's watching decides what "retry" should mean

`llm_client.structured_output` was built in Stage 3 deliberately without a
retry loop, with its own docstring stating plainly that "what 'retry'
should mean here... depends on Stage 5's loop guardrails, which don't
exist yet." This component is the first place in Stage 5 that had to
actually answer that question, and the answer it reached is a general
principle worth naming on its own, not just a justification specific to
charters: **the correct retry mechanism depends on whether a human is
present to notice and correct a bad output in real time.**

For charter creation, a human is *always* present at exactly the moment a
bad parse would surface — she is sitting at the terminal, about to decide
whether to confirm. `scripts/set_charter.py` therefore has no automated
retry-with-feedback logic (catch a validation failure, feed the error back
to the LLM, try again automatically) — if `parse_charter` produces
something wrong, or the universe resolves empty, the script surfaces that
plainly and exits, and re-running it with clearer wording *is* the retry.
This was a deliberate choice, not a shortcut standing in for a more
sophisticated mechanism that wasn't built yet: for a step where a human is
already present and watching, having her retry in plain language is the
more honest mechanism, not a lesser one — an automated retry loop would be
solving a problem (nobody available to notice a bad output) that doesn't
exist at this point in the system.

This is exactly the distinction that will matter again, differently, in
Component 6. The autonomous execution loop's `decide_next_action` runs
unattended — nobody is watching it iterate in real time the way she
watches a charter confirmation. There, if a structured output comes back
malformed or a tool call is invalid, human-mediated retry isn't available
as a mechanism at all, because there's no human present at that moment to
mediate it. That's the point at which `llm_client` will actually need the
automated retry-with-feedback logic its own docstring deferred — not
because charter-creation's approach was incomplete, but because the two
components sit on opposite sides of the same question. Component 2 is
where "who's watching" first had to be answered concretely (a human, so
retry stays manual); Component 6 will be where the other answer (nobody,
so retry has to be automated) actually gets built.

### `Settings` gap, hit a second time

Adding `TAVILY_API_KEY` to `.env` — done ahead of Component 3, while
fetching the real LangSmith key — immediately broke `Settings()` with the
identical `extra_forbidden` error Component 1 already hit and fixed once
for the three LangSmith variables. `pydantic-settings`'s `BaseSettings`
rejects any `.env` variable that isn't declared as a field, and this is
now the second time a new externally-sourced API key has needed the same
fix: declaring `tavily_api_key: str | None = None` on `Settings`, even
though no application code reads it through that object.
Confirmed — by reading `tavily-python`'s own `Client.__init__` in
`tavily/tavily.py` before relying on it, not assumed from the package name
— that `tavily-python`, like `langsmith`, falls back to
`os.getenv("TAVILY_API_KEY")` on its own when no explicit key is passed.
This is now a recognizable, repeating pattern rather than a one-off: this
project's `Settings` class needs a declared field for *every* key added to
`.env`, even when the SDK consuming that key never goes through `Settings`
at all — the field's only job is keeping `Settings()`'s strict validation
accurate as a mirror of `.env`'s actual contents. Component 3, which will
be the first component to actually *use* the Tavily key, inherits a
correctly-configured `Settings` rather than hitting this same failure a
third time.

## 4. Concepts introduced

**Grounding a field vs. grounding a combination.** A prompt that lists
valid values for two separate fields guarantees each field, in isolation,
only takes a real value. It does not guarantee that the *pairing* the
model chooses across those two fields corresponds to anything real, unless
the valid pairings are what's actually shown. This is a general lesson
about prompting any structured extraction task with more than one
interrelated field — independence in the prompt's presentation implies
independence in what the model will feel free to combine, whether or not
the underlying data is actually independent.

**Point-in-time universe screening**, briefly, since `resolve_universe`
exercises it for the first time: `screen()` takes an `as_of` date and only
considers price data up to that date, defaulting here to `date.today()`.
This is the same "screening on today's data to pick a universe, then
backtesting from 2015" lookahead concern `docs/architecture.md` §5 names,
applied here just to seed an initial candidate list for hypothesis
generation. The deeper discipline — making sure a *historical* backtest
window doesn't reuse today's screening result — is not this component's
job; it belongs to whichever later component actually chooses a historical
in-sample/out-of-sample window (Component 5, the study design).

## 5. How this component was verified

Every claim here was checked against a real run, not asserted from the
design reasoning alone. Four real `llm_client.structured_output` calls
were made against Bedrock (not mocked) across the four mandates described
in section 3, and every one of their outputs — the parsed universe filter,
the resolved ticker list, the `blocked` flag — was printed and read
directly, not inferred. The zero-match block was verified twice: once
through the raw Python function call (`create_charter` returning
`blocked=True`), and once by actually running `scripts/set_charter.py` as
a subprocess with the triggering mandate piped through stdin, confirming
the real printed block message and the real exit code (`1`) — not just
that the underlying function returned the right boolean. The
sector/industry mismatch's root cause was confirmed by directly querying
`ticker_metadata` for every row where `industry='Consumer Electronics' OR
sector='Consumer Cyclical'`, which is what established precisely that
`Consumer Electronics` only ever pairs with `Technology`, rather than
inferring that from the failure alone. The fix was verified by re-running
the *identical* mandate that had failed, not a new, easier one — confirming
the specific defect was closed, not just that a different case happened to
pass. The full pre-existing test suite (`pytest -q`, 220 tests) was re-run
after every meaningful code change in this component and stayed green
throughout, including after both `Settings` fixes.

The confirm path was verified with the same rigor as the block path, in a
follow-up round specifically because the first pass had only proven the
failure branch. `scripts/set_charter.py` was run for real with the fixed
"consumer tech companies" mandate piped through stdin followed by a real
`y` answer at the confirmation prompt — not simulated, the actual script
process, the actual prompt. The script printed "Confirmed. The agent may
now start work under this charter," and rather than trusting that printed
claim, the row was queried directly from `strategy_research`:
`confirmed = t`, `created_at = 2026-08-22 14:04:23.319157`,
`confirmed_at = 2026-08-22 14:04:23.325095` — a few milliseconds after
`created_at`, exactly as expected, since `create_charter` persists the
unconfirmed row first and `confirm_charter` updates it moments later once
the real answer comes back from the prompt. The `charter` JSONB column's
contents were also read directly from the row and matched what the
terminal had printed (`sector='Technology'`, `industry='Consumer
Electronics'`, `resolved_universe=['AAPL']`) — confirming the persisted
data, not just the in-memory object the script held before writing it.

**What this does not prove.** All five real-model runs used mandates
constructed to test specific things (a genuine-but-obscure real match, a
genuine non-match, the one that found the pairing gap, and its confirmed
re-run) — this is not a systematic sweep over many mandate phrasings, and
it's entirely possible other prompt-and-real-data combinations would
surface a different gap the same way this one did, undiscovered until
something like this same kind of adversarial testing finds it. The pairing
fix closes the specific mechanism found here (independent field lists
implying independent choice) but provides no structural guarantee against
a different kind of grounding gap this testing didn't happen to probe.

## 6. Interview defense

**Q: Why does `resolve_universe` call `screen()` directly instead of going
through the real MCP server you built in Stage 4 specifically so tools
would be reachable that way?**

A: Because MCP's tool-registration boundary exists to discipline something
this component never does — an LLM choosing which tool to call. Here,
code calls the screener deterministically, every time, with arguments
already fully determined before the call happens; there's no choice for
the MCP layer to discipline. Component 6's actual agent loop will go
through real MCP for exactly the same tool's logic, because that's where a
choice genuinely exists. The tool logic underneath (`screen()`) is
identical either way — what MCP adds is a boundary around *deciding* which
tool to call, not a boundary tool logic itself needs to pass through
unconditionally.

**Q (hard): You found a real hallucination-adjacent defect during testing,
fixed it, and re-ran the same mandate to confirm the fix. How do you know
there isn't a second, similarly subtle gap in this same grounding approach
that this testing simply didn't happen to surface?**

A: I don't, and it would be dishonest to claim otherwise. The four real
mandates run here were chosen to probe specific things — a real, correctly
matched industry; a case with genuinely no real match; and the one that
found the sector/industry pairing gap — not a systematic search over
mandate phrasing. The actual guarantee this component provides doesn't
rest on prompt grounding being perfect; it rests on the code-level
zero-match block being unconditional. Even if a *different* prompting gap
exists that this testing didn't find, the same mechanism that caught the
"consumer tech" mismatch will catch any other combination that resolves to
zero tickers, because that check doesn't depend on which specific words
produced the bad parse — it depends only on the resolved universe actually
being empty. The honest claim is: layer 1 (grounding) is best-effort and
demonstrably imperfect even after one round of hardening; layer 2 (the
block) is unconditional and unaffected by which specific gap in layer 1
produced the bad combination.

**Q: Why didn't you just have `parse_charter` retry automatically when
`create_charter` comes back `blocked`, instead of making her re-run the
script by hand?**

A: Because a human is already present and watching at exactly the moment a
bad parse would surface — she's sitting at the terminal, about to decide
whether to confirm. Building automated retry-with-feedback here would be
solving a problem (nobody available to notice and correct a bad output)
that doesn't actually exist at this point in the system. It would also add
real complexity — deciding what feedback to give the LLM, how many retries
to allow, what "still wrong after N tries" should even mean — to solve a
problem her simply rephrasing the mandate already solves for free. This
mechanism becomes genuinely necessary in Component 6's autonomous loop,
where nothing is watching in real time and human-mediated retry isn't an
available option at all — that's a different point in the system with a
different answer to the same underlying question, not a corner cut here.

**Honest weaknesses, stated plainly:** the grounding-gap discovery was a
product of the specific adversarial mandates tried, not a systematic proof
of completeness — a differently-shaped gap could still exist. Five real,
test charters (four unconfirmed, one now genuinely confirmed) sit in the
actual `strategy_research` database from this testing, left in place
rather than cleaned up — consistent with how Stage 4's own manual
verification also ran against real production data without a cleanup
step, but worth naming rather than leaving implicit.

## 7. What comes next and why

Component 3 (tiered RAG grounding) is next, and it shares nothing
structurally with this component except the `EffectFamily` enum — the same
one `ParsedCharter.hypothesis_families` uses here will tag each curated
paper in the corpus, which is what keeps a hypothesis's stated family and
the corpus's own categorization from drifting into two separate
vocabularies later. If this component's charter schema is wrong in a way
that matters, the most likely place it would first surface is Component 4
(hypothesis generation), which is the first component that actually reads
a confirmed `Charter.resolved_universe` and `hypothesis_families` to do
real work with — an empty or malformed universe reaching that far would
mean this component's zero-match block had a gap of its own, which
nothing found in this component's own testing suggests, but which
Component 4 would be where it would become visible if it existed.
