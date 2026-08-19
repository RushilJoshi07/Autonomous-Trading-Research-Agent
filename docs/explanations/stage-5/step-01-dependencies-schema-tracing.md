# Stage 5, Component 1 — Dependencies, schema, tracing scaffolding

## 1. What this component does

This component builds the foundation Stage 5's later components stand on: the
Python libraries the agentic core will need, a working vector-search
extension in Postgres, the eight new database tables that will hold every
structured object the agent produces (charter, hypothesis, study design,
study run, tool-call trace, verdict, scoreboard entry, corpus paper/chunk),
and a tracing hook so every LLM call becomes inspectable once real nodes
exist.

**What it explicitly does not do:** no charter gets parsed, no hypothesis
gets generated, no LLM makes a decision, no LangGraph node runs. Every table
this component creates is empty when it finishes. This is deliberately pure
infrastructure — Stage 5 is the first stage with an LLM in the runtime path
at all, and this component still has zero LLM calls in it (the one
`@traceable` decorator added to `llm_client.structured_output` doesn't add a
new call, it just instruments a function that already existed since Stage
3). The reason to build this first, separately, rather than folding schema
design into whichever component first needs a given table: every later
component (2 through 10) needs *some* subset of this schema to exist before
its own code can even import cleanly, so getting the shape right once, with
its own explain-and-verify pass, avoids each later component quietly
making its own ad hoc schema decisions under time pressure.

## 2. Every meaningful line explained

### The pgvector build

```bash
git clone --branch v0.8.6 --depth 1 https://github.com/pgvector/pgvector.git
make PG_CONFIG=/opt/homebrew/opt/postgresql@16/bin/pg_config
make install PG_CONFIG=/opt/homebrew/opt/postgresql@16/bin/pg_config
```

`--branch v0.8.6` pins the source build to the exact same version Homebrew's
own (Postgres-17/18-only) bottle ships, rather than pulling `main` — the goal
was reproducing what Homebrew *would* have installed if it supported
Postgres 16, not picking up whatever the newest unreleased pgvector commit
happens to be. `PG_CONFIG=...` is the one argument that matters to both
`make` invocations: pgvector's build system (PGXS, Postgres's standard
extension-build tooling) uses `pg_config` to discover which Postgres
installation's headers, library paths, and extension directory to build
against and install into. Without it, `make` falls back to whatever
`pg_config` is first on `$PATH` — which on this machine is also
`postgresql@16`'s (confirmed with `which pg_config` before running anything),
so the explicit argument was redundant in this specific case, but leaving it
implicit would have made the command silently depend on `$PATH` ordering
never changing, the same class of ambient-environment fragility
`llm_client`'s `aws_profile` parameter was written to avoid (see Stage 3).
`make install` (not plain `make`) is the step that actually copies
`vector.dylib`, the `.control` file, and every version-upgrade `.sql` script
into `postgresql@16`'s own `share/extension/` and `lib/postgresql/`
directories — `make` alone only compiles, it doesn't place files anywhere
Postgres would find them.

### `src/agentic_core/db/models.py`

The file has nine classes; rather than walk all nine field-by-field (mostly
repetitive), here is what's non-obvious about the shape decisions that
recur across several of them:

```python
from data_pipeline.db.models import Base
```

Every new class subclasses this *specific* `Base`, imported from the
existing `data_pipeline` package, not a fresh `declarative_base()` call.
SQLAlchemy's `Base.metadata` is the single object Alembic's `env.py` and
`init_db.py`'s `create_schema()` both already depend on (`target_metadata =
Base.metadata`, `Base.metadata.create_all(engine)`). A second, independent
`Base` would have its own, separate `.metadata` — Alembic would simply never
see any table registered on it, and `alembic revision --autogenerate` would
generate an empty, useless migration with no error or warning to explain
why. This was confirmed as the actual risk, not assumed: after writing the
models, `Base.metadata.tables.keys()` was printed directly and checked
against the expected 14 names *before* running autogenerate, specifically to
catch this failure mode before it could produce a silently-empty migration.

```python
grounding_tier: Mapped[str] = mapped_column(String(16))  # local_corpus / whitelist_search / none
status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed / testing / confirmed / rejected / inconclusive
```

Every status-like field across all nine tables follows this exact
convention — a plain `String(16)` with the allowed values spelled out in a
trailing comment, never a Postgres `ENUM` type or a Python `enum.Enum`. This
matches `IngestionRun.status` exactly (Stage 1). The reason to keep matching
it here rather than "upgrading" to a real enum type: Postgres `ENUM` columns
require their own `ALTER TYPE ... ADD VALUE` migration every time a new
status value is needed, and — more sharply — `ADD VALUE` cannot run inside
the same transaction as other DDL in some Postgres versions, which
complicates exactly the kind of straightforward, one-shot Alembic migrations
this project has used for every schema change so far. A plain string with
Pydantic (application-layer) enforcement upstream costs nothing at read
time and never forces a migration just to add `"decayed"` as a new
`ScoreboardEntry.status` value later.

```python
charter: Mapped[dict] = mapped_column(JSONB)
```

vs., on the same class:

```python
confirmed: Mapped[bool] = mapped_column(default=False)
```

This is the line drawn everywhere in this file, and it's worth being
explicit about where it falls: **JSONB for anything whose internal shape is
still owned by a component that hasn't been designed yet**
(`Charter.charter` — Component 2's job; `Hypothesis.rule`,
`.falsification_condition`, `.citations` — Component 4's; `StudyDesign.design`
— Component 5's; `Verdict.claims` — Component 7's). **A real typed column
for anything already settled by `docs/architecture.md` itself** — `confirmed`
is a plain boolean because Step 1 of the user journey already fixes its
meaning ("that confirmation flag is what allows the agent to start");
`grounding_tier` is a real column, not buried inside a JSONB blob, because
Component 7's statistical correction has to read it directly to pick a
significance multiplier, and a value a downstream *decision rule* branches
on belongs in a column code can query, not a key inside a document it has to
parse first. Section 3 below covers why this line was drawn here rather than
either extreme (fully normalizing everything now, or JSONB-ing everything
including the settled fields).

```python
arxiv_id: Mapped[str] = mapped_column(String(32), primary_key=True)
```

`CorpusPaper` is the one table in this file using a natural key (the arXiv
ID itself) as its primary key, rather than a generated UUID. Every other new
"identity" table (`Charter`, `Hypothesis`, `StudyDesign`, `StudyRun`,
`Verdict`, `ScoreboardEntry`) uses `String(36)` with a `uuid.uuid4()`
default, matching `IngestionRun`'s own precedent from Stage 1. The
difference: an arXiv ID is already a genuinely unique, externally-assigned
identifier for exactly this entity — the same reasoning `PriceBar` already
uses for its own `(ticker, date)` composite key rather than a surrogate ID.
Generating a UUID for `CorpusPaper` would have meant maintaining two
identifiers for the same paper (the UUID, and the arXiv ID needed anyway to
avoid re-fetching a paper already ingested) for no benefit.

### `migrations/env.py` and `src/data_pipeline/db/init_db.py`

```python
import agentic_core.db.models  # noqa: F401 -- registers Stage 5's tables onto the same Base.metadata
```

One line, added identically to both files. Neither file uses the imported
name — the `# noqa: F401` comment tells the linter that's intentional, not
an oversight. The import exists purely for its *side effect*: importing a
module that defines SQLAlchemy model classes is what causes those classes to
register themselves onto `Base.metadata` in the first place (SQLAlchemy's
declarative system does this registration at class-definition time, which
only happens once the module is actually imported somewhere). Both files
needed the same fix because they're the two places in the whole project that
compute "every table that currently exists" from `Base.metadata` — Alembic's
`env.py` for migrations, `init_db.py` for the from-scratch `create_all()`
path — and both would otherwise have kept seeing only the original six
`data_pipeline` tables, with no error to indicate anything was missing.

### The migration's two hand fixes

```python
import pgvector.sqlalchemy
```

Alembic's `--autogenerate` wrote `pgvector.sqlalchemy.vector.VECTOR(dim=384)`
into the generated `create_table('corpus_chunks', ...)` call (visible in the
raw generated file) but did not add an import for `pgvector` anywhere in the
file — it only auto-imports from a fixed list of modules it already knows
about (`sqlalchemy`, `alembic.op`, the postgresql dialect), not arbitrary
third-party column types a model happens to use. Left as generated, running
this migration would have failed immediately with a `NameError` the first
time `alembic upgrade head` tried to execute it — a failure that autogenerate
itself doesn't warn about, since it doesn't execute the file it just wrote.
This was caught by reading the generated file before running it, not by
hitting the error.

```python
op.execute("CREATE EXTENSION IF NOT EXISTS vector")
```

Placed as the first line of `upgrade()`, before any `create_table` call.
Autogenerate compares Python model metadata against the live database's
table/column/index structure — extensions aren't part of that comparison at
all, so there was never a chance autogenerate would produce this line on its
own; it had to be added by hand, informed by the earlier discovery (this
same session) that the extension wasn't available until it was compiled from
source. `IF NOT EXISTS` makes the statement idempotent, which matters
because the extension had already been created manually in both databases,
for verification, before this migration was ever generated — without `IF NOT
EXISTS`, applying this migration would have failed on a database where the
manual verification step had already run.

## 3. Design decisions and rejected alternatives

### Building pgvector from source, not upgrading Postgres or dropping vector search

The actual blocker, once found, admitted three genuinely different fixes,
and this was put to the user directly rather than picked silently — the
reasoning is worth restating here since it's the component's biggest single
decision. **Upgrading Postgres 16 → 17** would have made pgvector fully
Homebrew-managed again (auto-updates, no manual build step to remember), but
means running `pg_upgrade` or a dump/restore against `strategy_research`,
which holds every row of real market data ingested since Stage 1 — real
surgery on a database with real consequences if done carelessly, and not
something to fold into a Stage 5 schema component as a side effect.
**Dropping pgvector for a Python-side brute-force cosine scan** over a plain
array column would have avoided touching Postgres or building anything from
source at all, and was genuinely viable at this corpus's scale (a few
thousand vectors, well within where a linear scan in `numpy` is still
fast) — but it means Component 3's retrieval code carries hand-rolled
similarity-search logic instead of using the tool docs/architecture.md §7
already named for this job, and it's harder to reverse later (once
`retrieve_local` is written against one storage shape, re-platforming it
onto real `pgvector` is strictly more work than building the extension once,
now, before any code depends on the alternative). **Building from source**
was the one option that fixed the actual gap — a missing Postgres-16 build
of an already-decided library — without touching the existing database
version or its data at all; the cost is that this one extension now sits
outside Homebrew's management (a real, stated liability — see the interview
defense section).

### JSONB now, typed columns later — not the reverse, and not JSONB for everything

The alternative to the line drawn in section 2 would have been fully
normalizing every field Components 2, 4, 5, and 7 will eventually need —
inventing, for instance, exact columns for every part of `Hypothesis.rule`
right now, in a component whose only job is schema scaffolding. That was
rejected for a concrete reason, not just "simpler for now": `Hypothesis.rule`
reuses Stage 3's own `StrategyRule` schema, which is itself a recursive
Pydantic discriminated union (`Condition` → `Comparison` → `IndicatorTerm` /
`ConstantTerm` / ... in `backtester/schema.py`). Normalizing that into
relational columns today, before Component 4 has even decided exactly how a
`Hypothesis` wraps a `StrategyRule`, risks designing the wrong shape and
having to re-migrate once Component 4's own design pass actually happens.
The opposite extreme — JSONB for everything, including fields like
`confirmed` or `grounding_tier` that are already fully specified — was also
rejected: those fields get read and branched on directly by code in later
components (the dedup check in Component 4, the significance correction in
Component 7), and a value that gets queried or compared belongs in a column
the database itself can index and type-check, not a key pulled out of a
parsed JSON blob every time. The dividing line is not "structured vs.
unstructured data" in the abstract — it's "has this field's shape already
been decided by an authority above this component (the architecture
document), or does it belong to a design decision a specific later component
still owns."

### The `study_runs` split (added mid-component, at the user's prompt)

The original schema had `tool_call_traces` and `verdicts` foreign-keying
directly to `hypotheses`. The user asked, before any code was written,
whether inserting a `StudyRun` between `Hypothesis` and its downstream
tables now — genuinely free for Stage 5's own logic, or a real complication
— since `docs/architecture.md` Step 7 already names a later feature
("re-test under 2020 only" spawning "a new study") that this stage's schema
should be able to support without a redesign, even though Stage 5 itself
never creates more than one `StudyRun` per hypothesis. Worked through
concretely rather than answered by feel: the change touches exactly two
places in the *component* work still to come — Component 5's `design_study`
node gains one extra insert (the `StudyRun` row, alongside the `StudyDesign`
row it was already going to write), and Component 7's `synthesize_verdict`
node gains one extra update (flipping `study_runs.status` to `'completed'`).
Nothing about the `Action` discriminated union, the loop guardrails, the
out-of-sample enforcement, or the verdict validator's claim-checking logic
changes shape either way. It was accepted specifically because it turned out
to be additive, not because "more structure now" is generically good — the
opposite conclusion (reject it as premature) would have been correct if it
had required restructuring any of Stage 5's own decision logic, and the
plan explicitly said so before the answer was known.

There's a second, sharper reason beyond "it's free": the multiple-comparisons
correction (`research_stats.multiple_comparisons`, already built and tested
in Stage 4) exists to answer "how many independent chances at a false
positive has this charter had," which is fundamentally a count of *tests
run*, not *hypotheses proposed*. Under Stage 5's own schema those two counts
are identical today (exactly one test per hypothesis), so this makes no
observable difference yet — but the moment a later stage's re-testing
feature exists and one hypothesis gets tested three times under different
windows, `COUNT(DISTINCT hypothesis_id)` silently undercounts the real risk
while `COUNT(study_runs)` doesn't. Without this table, the natural query to
reach for later is the wrong one, and nothing would flag that it's wrong —
it would just quietly under-correct exactly the defense
`.claude/rules/agent-honesty.md` calls "the biggest threat to the honesty
claim." Adding the table now costs two extra writes; discovering the
undercounting bug after Stage 8's re-testing already shipped would cost a
migration plus, potentially, a batch of verdicts that used too lenient a
threshold.

### Keeping `Settings` strict rather than loosening it to accept the new `.env` keys

Adding `LANGSMITH_TRACING`/`LANGSMITH_API_KEY`/`LANGSMITH_PROJECT` to `.env`
broke `Settings()` immediately — `pydantic-settings`'s `BaseSettings` loads
every variable present in the configured `.env` file and, by default,
rejects (`extra_forbidden`) any that don't map to a declared field. This
project's `Settings` class had never hit this before, since all three prior
`.env` entries (`DATABASE_URL`, `DATABASE_URL_TEST`, `LOG_LEVEL`) already had
matching fields. The fix actually taken was to declare all three new
variables as real fields on `Settings`, even though no application code
reads them through that object — the `langsmith` SDK reads
`LANGSMITH_API_KEY` and friends directly from `os.environ` on its own
(confirmed by reading `langsmith/client.py`'s own lookup code before relying
on it, not assumed from the package name). The rejected alternative — setting
`model_config`'s `extra="ignore"` so unmapped `.env` keys are silently
skipped — would have fixed the same symptom with less code, but permanently
weakens what `Settings()` succeeding actually guarantees: right now, a typo
in `.env` (`DATBASE_URL` instead of `DATABASE_URL`) produces a loud
`ValidationError` at import time, before any code that depends on the typo'd
value ever runs. `extra="ignore"` would make that same typo silently produce
a `Settings` object missing the field the typo was trying to set, with no
error until whatever code reads `settings.database_url` fails later, further
from the actual mistake. Declaring the fields costs three lines and keeps
that guarantee intact for every `.env` key, not just the pre-existing three.

## 4. Concepts introduced

**Postgres extensions and PGXS.** Postgres ships a small, fixed set of
built-in types and functions; anything beyond that (full-text search
dictionaries, additional data types like `vector`, procedural languages)
is added as an *extension* — compiled code plus a `.control` file and a set
of SQL scripts, installed into the specific Postgres installation's own
`share/extension/` and `lib/postgresql/` directories, then explicitly turned
on per-database with `CREATE EXTENSION`. Crucially, an extension is compiled
*against* a specific major Postgres version's internal C API — that's why
Homebrew's pgvector bottle, built for Postgres 17 and 18, produced files
that simply don't exist for Postgres 16; it isn't a configuration problem,
it's a different binary that was never built. PGXS (`PG_CONFIG=...`, `make`,
`make install`) is Postgres's own standard build tooling for exactly this
situation — building an extension from source against whichever
`pg_config` you point it at, independent of how that Postgres was itself
installed.

**JSONB vs. a fully normalized schema.** Postgres's `JSONB` type stores
JSON in a decomposed binary form that supports indexing and querying into
specific keys, unlike the plain `JSON` type (which stores an exact text
copy and re-parses it every time). The concept worth understanding, beyond
"Postgres can store JSON," is *when* JSONB is the right tool: it fits a
value whose internal shape is validated once, at the application boundary,
by a schema that already exists for another reason (here, Pydantic models
each later component will write) and that the database itself never needs
to reach inside of during a query. It's a poor fit for anything the database
needs to filter, join, or index on directly — which is exactly why
`grounding_tier` and every `status` field in this schema are plain columns,
not nested inside a JSONB blob, even though they're conceptually "part of"
a hypothesis or a verdict.

**Import-for-registration-side-effect.** SQLAlchemy's declarative mapping
system (the `Base` class every model subclasses) registers a model class
onto its base's shared `.metadata` object at class-definition time — which,
in Python, means "the first time the module containing that class is
imported," not automatically, and not based on the class merely existing
somewhere on disk. This is why `migrations/env.py` and `init_db.py` needed
an explicit `import agentic_core.db.models` even though neither file
otherwise touches that module: without it, Python would simply never load
that file, and `Base.metadata` — which both Alembic and `create_all()`
treat as "the complete set of tables that should exist" — would silently
stay incomplete. This pattern (a small, deliberate list of "make sure these
are imported" statements at whatever code computes "all my models") is
standard in any codebase using more than one file's worth of SQLAlchemy
models against a single shared base; the alternative, a single flat models
file for the whole project regardless of how many unrelated domains it
covers, is what this component moved away from by giving Stage 5 its own
`agentic_core.db.models` rather than appending nine more classes onto
`data_pipeline.db.models`.

**Pre-registration integrity, and why it drives a schema choice.**
`.claude/rules/agent-honesty.md` requires that a hypothesis's falsification
condition be fixed *before* any test runs, specifically so the agent can't
retroactively rationalize a bad result. That's a behavioral rule about when
code is allowed to write the condition — but it becomes a much stronger
guarantee if the table storing it is also *structurally* write-once (no
`updated_at`, no code path that ever calls `UPDATE study_designs`). That's
the concrete reason `StudyDesign` stayed a separate table from the new
`StudyRun` rather than merging the two: a single merged table would need
mutable columns (`status`, `finished_at`) sitting right next to the
falsification condition, and nothing about the schema itself would then
rule out an accidental update touching both.

## 5. How this component was verified

Every claim in this file was checked against something real before being
trusted, not assumed correct because the reasoning sounded right:

- **pgvector, before compiling:** `SELECT * FROM pg_available_extensions
  WHERE name='vector'` against the live `strategy_research` database
  returned zero rows — confirmed the extension genuinely wasn't available,
  rather than assuming `brew install pgvector`'s "already installed and
  up-to-date" message meant it worked. Directly inspecting
  `Cellar/pgvector/0.8.6/share/` found only `postgresql@17` and
  `postgresql@18` subdirectories, which is what pinned down the actual root
  cause rather than guessing at one.
- **pgvector, after compiling:** the same query, re-run, returned
  `vector | 0.8.6`. `CREATE EXTENSION IF NOT EXISTS vector` was then run
  manually against both `strategy_research` and `strategy_research_test`,
  and `SELECT extname, extversion FROM pg_extension` confirmed `0.8.6`
  installed in both, before any migration was written to depend on it.
- **The models file:** before running `alembic revision --autogenerate` at
  all, `Base.metadata.tables.keys()` was printed directly and checked —
  all 14 expected table names (6 pre-existing + 8 new) were present, which
  is what confirmed the `import agentic_core.db.models` side-effect
  registration was actually working, rather than trusting it silently.
- **The migration:** generated against the real `strategy_research`
  database (not a blank/synthetic one), so autogenerate's diff reflects
  what's actually different, not what a fresh empty database would show.
  Read in full before running it, which is what caught both hand-fixes
  (the missing `pgvector.sqlalchemy` import, the missing `CREATE
  EXTENSION`) before either could fail mid-migration. Applied to both
  databases (`alembic upgrade head`, `alembic -x db=test upgrade head`);
  `\dt` confirmed all 8 new tables exist in `strategy_research`, and `\d
  corpus_chunks` confirmed the `embedding` column is genuinely
  `vector(384)`, not silently coerced to something else.
- **Regression:** the full existing test suite (`pytest -q`) was re-run
  after every meaningful change — once right after adding the `.env`
  entries (where it failed, correctly, on `Settings()`'s `extra_forbidden`
  check), and again after the `Settings` fix, where all 220 pre-existing
  tests passed.

**What this does not prove, stated plainly:** no row has been written to
any of the eight new tables yet — the schema's shape is confirmed to exist,
but not confirmed to actually hold the data each later component intends to
put in it (that's each component's own job to verify, e.g. Component 2 will
be the first real test that `Charter.charter`'s JSONB shape actually fits a
real parsed charter). The `corpus_chunks.embedding` column is declared as
`Vector(384)` on the assumption that `BAAI/bge-small-en-v1.5` (the embedding
model Component 3 is planned to use) outputs 384-dimensional vectors — that
number has not yet been verified against a real model output in this
component, since no embedding model has been loaded by any code yet; if
Component 3 discovers a different dimension, this is a column-type change,
not a rebuild of anything else in this file. And the `@traceable` decorator
on `structured_output` has only been confirmed to not break anything when
tracing is off (no `LANGSMITH_API_KEY` set) — its actual tracing behavior,
with a real key, is completely unverified until the user adds one and a
real `structured_output` call is made.

## 6. Interview defense

**Q: Why does `Hypothesis.rule` sit in a JSONB column instead of its own set
of relational columns, when `StrategyRule` already exists as a well-defined
Pydantic schema in `backtester/schema.py`?**

A: Because "well-defined" and "already fully decided for this new context"
aren't the same thing. `StrategyRule` was designed in Stage 3 to describe a
strategy for the *backtester*; Component 4 hasn't yet decided exactly how a
`Hypothesis` wraps one — whether it stores the rule alone, or the rule plus
some hypothesis-specific metadata that also needs to live at that level.
Normalizing now would mean picking that shape before the component that
owns the decision has made it, and re-migrating if it turns out wrong.
JSONB storage plus Pydantic validation at the point of insertion is exactly
as safe — nothing invalid can reach the table either way — and it costs
nothing to change the internal shape later since the column doesn't encode
one.

**Q (hard): You compiled pgvector from source instead of getting it through
Homebrew. Doesn't that make this project's setup harder to reproduce — what
happens when someone else, or you on a different machine, tries to set this
up following `CLAUDE.md`'s documented setup steps?**

A: This is a real, honest gap, and it's worth naming rather than glossing
over: `CLAUDE.md`'s "Fresh environment setup" section currently documents
`createdb` and `python -m data_pipeline.db.init_db` and `alembic stamp
head` — it does not yet mention that `vector` needs to be built from source
for anyone running Postgres 16 specifically (the problem disappears for
anyone on Postgres 17+, where the ordinary Homebrew formula just works).
Someone following the documented steps today, on Postgres 16, would have
`brew install pgvector` appear to succeed — it really does install, just
files for the wrong Postgres version — and get no error at all until
`alembic upgrade head` tries to run `CREATE EXTENSION vector` and fails with
a confusing "extension \"vector\" is not available" error, with nothing in
the repo yet explaining why. The honest fix is documentation, not code: this
build-from-source step needs to be added to `CLAUDE.md`'s fresh-setup
section as its own explicit step, the same way `createdb`'s non-idempotency
is already called out there — and that hasn't been done yet as of this
component. It's a genuine loose end, not a hidden one.

**Q: Why did you keep `Settings`' default strict validation (`extra`
forbidden) instead of just telling it to ignore variables it doesn't
recognize, when the `langsmith` SDK doesn't even go through `Settings` to
read them?**

A: Because the strictness isn't really about these three specific
variables — it's a property of the whole file that was worth not degrading
as a side effect of adding them. Right now, if `.env` has a typo'd key
anywhere, `Settings()` throws immediately, at import time, before any code
that depends on the correctly-spelled version ever runs — which is exactly
the kind of fail-fast, "malformed input never proceeds" posture this project
already applies everywhere else (Pydantic validation on every LLM output,
rule validation before a strategy reaches the backtester). Silencing unknown
keys to fix one addition would have silently disabled that same protection
for every future key, not just these three.

**Q: Why introduce a whole `StudyRun` table for something Stage 5 only ever
uses in a 1:1 relationship with `StudyDesign` — isn't that exactly the kind
of premature abstraction the project's own working agreement warns against?**

A: It would be, if it had cost anything real in this stage — that's the
actual test that was applied before accepting it, not "more structure is
generically better." It was accepted specifically because working through
it concretely showed zero cost to Stage 5's own component logic (no
guardrail, validator, or decision rule changes shape) and a real, named
correctness benefit once Step 7's re-testing feature exists later (the
multiple-comparisons count needs to be per-test, not per-hypothesis, and
those diverge exactly when re-testing starts). If the answer had come out
differently — if adding it meant Component 6's loop needed new branching
logic, say — the right call would have been to reject it and let a future
stage pay for its own migration when it actually needs the concept.

## 7. What comes next and why

Component 2 (the charter) is next, and it's the first component that
actually writes a row anywhere in this new schema — `Charter.charter`'s
JSONB column gets its first real content, and `confirmed`/`confirmed_at`
get exercised for the first time by an actual confirmation flow rather than
sitting as untested column definitions. If this component's schema design
is wrong in a way that matters, Component 2 is where the first sign of it
would most likely appear: either Pydantic's `Charter` model (Component 2's
own job to design) doesn't map cleanly onto what was anticipated for the
JSONB column, or persisting a real charter surfaces a constraint this file
got wrong (a `String` length too short, a foreign key pointed the wrong
direction). The columns least likely to need revisiting are the ones this
file spent the most explaining on precisely because they were already
settled by `docs/architecture.md` rather than invented here — `confirmed`,
`grounding_tier`, the FK chain through `study_runs`. The ones genuinely at
risk are the JSONB columns, by design: they're intentionally undecided
until the component that owns each one gets its own explain-first pass,
which is the entire point of not normalizing them now.
