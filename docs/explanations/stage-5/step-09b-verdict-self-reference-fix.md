# Stage 5 Component 7 follow-up — the verdict validator's self-reference false positive

> Not a new component. This is a real bug found and fixed in already-shipped,
> already-gated code (`agentic_core/verdict.py` — Sacred Gate 2), discovered
> live during Stage 7's end-to-end gate walkthrough. `decide_status`,
> `validate_claims`, and `render_verdict`'s overall shape are unchanged and
> already explained in full in `step-09-verdict.md` — this document covers
> only what changed and, more importantly, *how* a change to Sacred Gate 2
> code gets made safely once the project is already relying on it.

---

## 1. What this fixes

`scan_for_unreferenced_numbers` — the half of Sacred Gate 2 that catches a
fabricated number hiding in the verdict's prose even when the claims list
itself is clean — was rejecting real, honest verdicts for a reason that had
nothing to do with fabrication. A hypothesis auto-named
`52W_High_Proximity_Momentum_NVDA_MSFT` naturally got described in its own
verdict as "the 52-week-high proximity momentum strategy," and the number
scanner flagged `"52"` because it had no way to tell a digit that's part of
an already-validated hypothesis *name* apart from a fresh, unvalidated
evidentiary claim.

**Not in scope:** anything about how `decide_status` decides confirmed vs.
rejected, or how `validate_claims` checks a claim against its trace — both
untouched, both already covered in `step-09-verdict.md`. This document is
specifically the number-scanning half, and specifically the one new
exception carved into it.

---

## 2. Every meaningful line explained

```python
_NAME_DIGIT_LETTER_RE = re.compile(r"(\d+(?:\.\d+)?)([A-Za-z])")

def _self_reference_digit_letters(hypothesis_name: str) -> dict[float, str]:
    return {float(m.group(1)): m.group(2).lower() for m in _NAME_DIGIT_LETTER_RE.finditer(hypothesis_name)}
```

Finds every place in the hypothesis's own name where a digit run is
immediately followed by a letter — `"52W"` in
`"52W_High_Proximity_Momentum"` — and records `{52.0: 'w'}`. It stops there
deliberately: it does not try to turn `'w'` into the word `"week"`. A
hardcoded table (`w` → week, `d` → day, `m` → month, ...) would need
updating by hand every time a future hypothesis name used a unit letter
nobody anticipated, and would silently do nothing useful the one time that
happened. Recording the bare letter and matching on it later (next function)
generalizes to any letter for free.

```python
_TIME_UNIT_WORDS = {"day", "days", "week", "weeks", "month", "months", "quarter", "quarters", "year", "years"}
```

A small, closed, stable set — deliberately not the bare digit itself.
Allowlisting `"52"` directly would have been the easy fix and the wrong one:
it would let a genuinely fabricated `"a 52% win rate"` slide through
unvalidated just because a completely different, legitimate `"52"` happens
to appear elsewhere in the same hypothesis's name. Checking *which word*
follows the number is what keeps the exception narrow. `"session"`/
`"sessions"` was considered and left out — not assumed absent, checked: a
grep of `src/backtester/schema.py` and `src/backtester/extended_indicators.py`
(every place this codebase actually defines an indicator or period word)
turns up nothing. It's absent from the set on that evidence, and the code
comment says so directly, so a future reader doesn't have to re-derive
whether it was an oversight or a decision.

```python
def _is_self_reference(narrative: str, position: int, letter: str) -> bool:
    tail = narrative[position : position + 16].lower()
    gap = _SELF_REFERENCE_GAP_RE.match(tail)
    rest = tail[gap.end() :]
    word_match = _WORD_RE.match(rest)
    word = word_match.group(0) if word_match else ""
    return word in _TIME_UNIT_WORDS and word.startswith(letter)
```

`tail = narrative[position : position + 16].lower()` lowercases the *whole*
slice before any regex runs — this is what makes `"52-Week-high"`
(capital `W`, even sentence-initial) resolve correctly: case is normalized
first, not depended on to already be right. `_SELF_REFERENCE_GAP_RE`
(`^[\s-]{0,2}`) allows up to one hyphen or space between the number and the
word, covering `"52w"`, `"52-week"`, and `"52 week"` with one rule instead
of three. `_WORD_RE = re.compile(r"[a-z]+")` is greedy, so `.match(rest)`
captures the *entire* contiguous run of letters right there — `"weekend"`
or `"weeklong"` in full, never truncated to `"week"` — before
`word in _TIME_UNIT_WORDS` ever runs. Because Python set membership is exact
equality, `"weekend" in _TIME_UNIT_WORDS` and `"weeklong" in
_TIME_UNIT_WORDS` are both `False`. This means the function was already a
whole-word match, never a prefix match, from the moment the closed-set
check was added — but that was true by construction of the greedy regex,
not because anyone had verified and written it down. `word.startswith(letter)`
is the second, independent condition: the matched word must also share the
first letter recorded from the hypothesis's own name, so a `"52W"`-named
hypothesis's `"52"` can be exempted by `"week"`/`"weeks"` but not by
`"years"` — a real time-unit word, just the wrong one for what this name
actually abbreviated.

```python
def scan_for_unreferenced_numbers(
    narrative: str, claims: list[Claim], allowed: set[float], hypothesis_name: str = ""
) -> list[str]:
    ...
    self_ref_letters = _self_reference_digit_letters(hypothesis_name)
    orphans = []
    for match in _NUMBER_RE.finditer(narrative):
        token = match.group(0)
        value = float(token)
        if any(_close(value, c) for c in claimed):
            continue
        if any(_close(value, a) for a in allowed):
            continue
        letter = self_ref_letters.get(value)
        if letter is not None and _is_self_reference(narrative, match.end(), letter):
            continue
        orphans.append(token)
    return [...]
```

Two changes to the function itself. `hypothesis_name: str = ""` — the
default means every pre-existing call to this function, anywhere, keeps
behaving exactly as before; `_self_reference_digit_letters("")` returns an
empty dict, so the new branch never fires unless a caller opts in.
`_NUMBER_RE.findall(narrative)` became `_NUMBER_RE.finditer(narrative)` —
`findall` only ever returned token *strings*, with no way to know *where*
in the narrative each one was found. Checking self-reference needs the
position immediately after the match (`match.end()`), so the switch to
`finditer` is what makes per-occurrence checking possible at all. That's
the point of doing it this way rather than checking "does `52` appear as a
self-reference anywhere in this narrative" once per unique digit: the exact
scenario this project's own review caught (Section 3) is a narrative where
the *same* digit is both a legitimate self-reference in one place and a
fabricated claim in another — a token-level check would have cleared both
occurrences the moment either one looked exempt.

---

## 3. Design decisions and rejected alternatives — three real rounds, not one

The mechanism above is the *final* state. It went through three rounds of
review before being trusted, and each round found something real — this
sequence, not just the resulting code, is the actual defensible story for
how a change to Sacred Gate 2 gets made without weakening it.

**Round 1 — matching on the first letter alone was not enough.** The very
first version of this fix checked only `rest[:1].lower() == letter`: does
the character right after the number match the abbreviation letter. Caught
immediately: `"winning"` and `"weighted"` also start with `'w'`, so a
fabricated `"the strategy closed 52 winning trades"` — a real number with no
backing claim at all — would have been silently exempted. **Chosen fix:**
require the following word to belong to `_TIME_UNIT_WORDS`, not just share
a first letter. **Alternative rejected:** widen the check to "any word
starting with the letter" was the *original*, broken design — the whole
point of this round was rejecting exactly that. **Cost of not catching
this:** a real fabrication-detection hole in Sacred Gate 2 itself, shipped
silently. `test_a_same_first_letter_word_does_not_count_as_a_self_reference`
locks this in.

**Round 2 — is the word match exact or a prefix?** A reasonable next
question: does the closed-set check itself only verify a *prefix* of the
following text, the same shape of bug as round 1 one level deeper —
`"weekend"`/`"weeklong"` both literally start with `"week"`. This was
checked empirically before answering — a throwaway script ran the actual
`_is_self_reference` function against both strings — rather than answered
by re-reading the code and reasoning about what it probably did. The
result: this was *not* actually a bug. The greedy `_WORD_RE` regex already
captured the whole contiguous word before the set-membership test ran, so
`"weekend"` and `"weeklong"` were already correctly rejected. **What
changed anyway:** the property was true but unstated and untested — the
docstring didn't say it, and nothing would have caught a future refactor
that broke it (say, swapping the greedy match for something that only
peeked at the first few characters). Documented explicitly and added
`test_a_number_followed_by_a_longer_word_starting_with_a_unit_word_is_not_a_self_reference`
to convert "true by accident of the current implementation" into "true by
contract, and checked."

**Round 3 — is the match case-sensitive?** `_WORD_RE` only matches
`[a-z]+` — lowercase. Would a capitalized `"52-Week-high"`, especially
sentence-initial, fail to match and silently reintroduce the original false
positive? Checked empirically again, against four real variants (lowercase
hyphenated, capitalized hyphenated sentence-initial, capitalized
space-separated, all-caps no-separator) run through the real function. Not
a bug: `tail = narrative[position:position+16].lower()` normalizes case
before the word regex ever sees the text. `test_a_capitalized_self_reference
_is_still_recognized` was added using the exact hyphenated,
sentence-initial form asked about — not the easier space-separated variant
— specifically so the committed regression test actually covers the case
that was in question, not a nearby one.

**The meta-decision underneath all three rounds:** every round was answered
by *running the actual function* against the actual disputed input first,
not by reading the code and describing what it should do. Rounds 2 and 3
turned out not to be bugs — but "I read it and it looks right" was never
treated as sufficient evidence for code whose entire job is catching
fabrication, and the two rounds that found nothing wrong still produced a
permanent test, because "verified once in conversation" and "locked in the
suite" are not the same guarantee against a future change.

---

## 4. Concepts introduced

**A self-reference vs. a claim.** Every number in a verdict's prose falls
into exactly one of three buckets this scanner now recognizes: a number
that matches a validated claim's value, a number that's a known structural
constant (window count, threshold, calendar year — the pre-existing
`allowed` set), or a number that's part of the hypothesis's own
already-validated name, echoed back in prose. Only the third bucket is new
here. The distinction matters because the first two buckets are about
*evidence* — did the backtester actually produce this number — while the
third is about *identity* — the hypothesis was already given this name, by
a process that was itself validated, before the verdict was ever written.
Conflating the third bucket with "unvalidated" is what produced the false
positive; conflating it with "anything goes" (a blanket digit allowlist)
is exactly what the multi-round review above existed to prevent.

**Closed-set membership vs. an abbreviation dictionary.** `_TIME_UNIT_WORDS`
looks similar to a dictionary but is doing a categorically different job.
A dictionary *maps* something (`'w'` → `"week"`) and has to be extended
every time a new key shows up. A closed set only *tests membership* — it
never has to answer "what does this letter mean," only "is this specific
word one of a small number of legitimate ones." The distinction is what
keeps the fix generalizable without needing to anticipate every unit letter
a hypothesis name might ever use.

---

## 5. How this was verified

**Unit-level, all in `tests/agentic_core/test_verdict.py`:** 30/30 pass,
including 5 new tests added across the three rounds above (listed in
Section 3) plus the pre-existing fabrication/rounding/structural-number
tests, unmodified and still passing — confirming the new exception didn't
loosen anything already covered. **Full suite:** 382/382.

**Against real, live data — not just synthetic unit tests.** The actual
pending study run this bug was discovered on
(`68e305b9-27fa-40ab-9569-a3f1d8920a8a`) was re-verdicted twice: once
right after the first fix, and again after Round 1's tightening — both
times by genuinely deleting the previously-written `Verdict` row first and
re-running `scripts/render_verdict.py` from scratch, not by trusting the
unit tests alone to stand in for the real case. Both times it succeeded on
the *first* real attempt (none of `render_verdict`'s own 3 internal retries
needed), writing a real `rejected` verdict whose narrative said
"52-week-high proximity" without being flagged.

**What this does not prove.** The fix is scoped to digit-followed-by-letter
name patterns (`"52W"`) and a five-word time-unit vocabulary. A hypothesis
name using a different naming convention — a digit *preceded* by letters
(`"RSI2"`), or a genuinely new kind of self-reference this project hasn't
produced yet — is not covered, and nothing in this fix would catch that
today; it would reproduce the original false positive under a new
disguise. That's a real, disclosed limitation, not a hidden one — the
`_TIME_UNIT_WORDS` comment says explicitly to extend it "once a real
hypothesis name actually uses" something new, meaning the current scope is
deliberately reactive to evidence rather than speculative.

**A related, separate live finding from the same walkthrough, worth
recording here since it happened in the same session:** driving
`propose_hypothesis` for real (there is no CLI script for it — confirmed by
grep, only `scripts/run_study.py` and `scripts/render_verdict.py` exist —
so it was called via a one-off driver script) rejected two real LLM
proposals in a row before a third succeeded: an unverified `QUANTILE`
indicator (present in `extended_indicators.py`'s registry but with
`verified=False` — Stage 3's build-time-proposed-but-never-execution-
confirmed indicator set correctly refusing to let an unverified entry into
a real rule) and an out-of-bounds `ROC` length. Both are live confirmations
that Stage 3's "the rule must be executable" guarantee — checked in
`IndicatorTerm._check_indicator`, `src/backtester/schema.py` — holds
against real, unconstrained LLM output, not only the fixtures its own test
suite already covered.

---

## 6. Interview defense

**"Walk me through how you found this."** Running Stage 7's real
end-to-end gate walkthrough for the first time — setting a mandate through
the actual UI, confirming it, then manually driving the still-disclosed-
manual backend pipeline since no automated trigger exists yet — a real
hypothesis's verdict rendering failed after exhausting all 3 of its own
retry attempts. The script only printed the top-level exception, so a
one-off script was written to catch `VerdictValidationError` directly and
print its `.errors`/`.narrative` attributes, which is what actually showed
the number scanner had flagged `"52"` inside `"52-week-high."` The
mechanical `STATUS: REJECTED` decision underneath was unaffected and had
been computed correctly the whole time — only the LLM's prose kept getting
rejected on a false pretext.

**Hard question: "You're the one who wrote the first version of this fix,
and it had a real hole in it. Doesn't that undercut the confidence you're
supposed to have in Sacred Gate 2 code?"** No — and conflating "the first
draft had a gap" with "the guardrail failed" gets the story backwards. The
guardrail here isn't the code in any single commit; it's the requirement
that a change to this file survive adversarial review and live
verification before it's trusted, and that process is exactly what caught
the gap before it shipped. The first version was reviewed, found wanting,
tightened, reviewed again, found subtly still-undocumented (not wrong, but
unverified), tightened again in documentation and tests, and reviewed a
third time on a completely different axis (case sensitivity) before
anyone called it done. A validator that got everything right on the first
try with no scrutiny would actually be *less* trustworthy evidence of
rigor than one that visibly survived three rounds of someone trying to
break it.

**"Why didn't you just add '52' to the existing `allowed` structural set
instead of building a whole self-reference mechanism?"** Because
`allowed` is a *global* set — a number in it is treated as legitimate
everywhere in the narrative, for every hypothesis under every charter,
forever. Adding `52.0` there would exempt a genuinely fabricated `"a 52%
win rate"` in this exact same verdict from ever being caught, and would do
so silently for every future hypothesis whose real Sharpe ratio, trade
count, or p-value happens to round to `52`. The self-reference mechanism
is deliberately narrower and re-derived per hypothesis: it only exempts a
digit that's *both* present in *this* hypothesis's own name *and*
immediately followed by a real time-unit word matching that name's own
abbreviation. That's a categorically smaller, self-expiring exemption
scoped to exactly the case that needed one.

**Honest weakness:** the fix's scope (digit-then-letter names, five time
words) is real but narrow, stated plainly in Section 5. It solves the
specific bug found, not every conceivable future collision between a
hypothesis's auto-generated name and the number scanner — and that's the
right tradeoff for code this sensitive: narrow, evidenced, and extended
only when a real case demands it, rather than broad and speculative.

---

## 7. What comes next and why

Nothing downstream changes behavior because of this fix — `render_verdict`'s
external contract (`(verdict_id, Verdict)` or `VerdictValidationError`) is
identical before and after. What changes is that a real hypothesis whose
name happens to embed a period abbreviation can now actually get a verdict
written on a normal first attempt instead of burning all of
`render_verdict`'s internal retries (and the real LLM calls that cost) on a
false pretext every time. If this fix were subtly wrong — say, the
letter-cross-check were dropped and any time-unit word became a blanket
exemption — the failure would not be loud: it would look like a slightly
higher rate of real, fabricated period-word claims quietly passing
validation, indistinguishable from normal variance without exactly the
kind of adversarial, multi-round review this document just walked through.
