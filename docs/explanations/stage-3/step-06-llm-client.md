# Step 6 — The Minimal LLM Abstraction (Stage 3)

## 1. What this does

`src/llm_client/__init__.py` is the first LLM call anywhere in this project.
`CLAUDE.md` and `docs/architecture.md` both record a deliberate amendment to
the original "Stages 1–3 use no LLM" rule, permitting exactly one bounded,
offline, build-time exception: proposing parameter bounds for pandas-ta
indicators outside the hand-verified core set (Component 8, not yet built).
This module is the thing that call will go through — a single function,
`structured_output`, that calls Claude and returns a validated Pydantic
object or raises.

What this component is *not*: nothing here is invoked yet. No trading
decision runs through it, no rule gets proposed by it, no runtime code path
in the backtester calls it. It's pure infrastructure, built and proven
working, waiting for Component 8 to be its first real caller.

---

## 2. A provider correction, made before writing anything

The Stage 3 plan's own text for this component says, verbatim: "Uses the
Anthropic SDK directly." Before writing any code, this was checked against
`docs/architecture.md` directly rather than trusted from memory — architecture
section 7 actually says the `llm_client` abstraction points "at **Bedrock**
(AWS credits) or the direct Anthropic API," with the provider choice
explicitly framed as "a one-line change rather than a rewrite," not an
exclusive mandate for either one. Neither document technically contradicts
the other — the plan's phrasing is a permissible instance of the more general
architecture, not a violation of it.

The user's stated intent — Bedrock, specifically, to spend AWS student
credits rather than pay for the direct Anthropic API out of pocket
(architecture.md's cost-discipline section) — was treated as authoritative
over the plan's more permissive default. This is the correct precedence: the
plan document is one instantiation of a decision the user actually owns:
whether the *document's* wording happens to allow the alternative or not,
the user stating their actual intent settles which one gets built. Re-reading
the document precisely first mattered here for a narrower reason — to confirm
following that instruction wasn't silently *contradicting* something already
settled, not to relitigate whether to follow it.

---

## 3. What was verified about the SDK before writing the call

The naive assumption about Bedrock support would be that it means hand-rolling
raw `boto3.client("bedrock-runtime").invoke_model(...)` calls against
Bedrock's own native request/response JSON envelope — a genuinely different
shape from the direct Anthropic API's Messages format, requiring separate
parsing logic entirely. Rather than build against that assumption,
`anthropic[bedrock]` was installed and the actual SDK objects were inspected
directly: `anthropic.AnthropicBedrock` is a client class living in the *same*
`anthropic` package (pulling in `boto3` transitively via the extra, no
separate dependency to manage by hand). Confirmed empirically — not from
documentation memory — that `AnthropicBedrock().messages` and
`Anthropic().messages` are the literal same `Messages` resource class, with
an identical `.create()` signature and an identical `Message` return type
annotation.

This is what makes architecture.md's "one-line change" claim concretely true
rather than aspirational: the entire call-and-parse body of
`structured_output` is provider-agnostic. Only the client constructor line
differs between Bedrock and the direct API — everything after it, including
every line discussed below, would work unchanged against either.

---

## 4. Design decisions

### Forced tool-use, not free-text JSON parsing

```python
tools=[{
    "name": tool_name,
    "description": f"Emit a validated {response_model.__name__}.",
    "input_schema": response_model.model_json_schema(),
}],
tool_choice={"type": "tool", "name": tool_name},
```

This is a single-shot reliability choice, independent of the retry decision
below. Rather than ask Claude to "respond with JSON matching this schema" in
prose and parse whatever text comes back (which can arrive wrapped in
markdown fences, preceded by explanatory prose, or subtly malformed), the
tool-calling mechanism structurally constrains the response to match
`response_model`'s own JSON schema. Verified offline, before ever making a
network call: `response_model.model_json_schema()` on a representative flat
Pydantic model produces a clean, directly-usable `input_schema` dict
(`properties`, `required`, `title`, `type`) — no transformation needed
between Pydantic's schema output and what the tool definition expects.

### No retry loop

The Stage 3 plan explicitly scopes retries as a Stage 5 addition: "Stage 5
extends this with prompt caching, retries, etc." This was honored rather than
built ahead of schedule, for two concrete reasons rather than one vague
"let's keep it simple." First, the *correct* retry pattern for a failed
tool-use validation isn't a plain follow-up message — the Messages API
requires a `tool_use` turn to be followed by a matching `tool_result` content
block referencing that call's ID, or the next request is rejected outright —
and that mechanic couldn't be verified against a live call before this
component's live testing even began. Second, a sensible retry cap needs a
budget to be capped *against*, and that budget is Stage 5's loop-guardrails
concern, not something to invent a policy for here.

`StructuredOutputError` was designed specifically so that deferral doesn't
foreclose the future work:

```python
class StructuredOutputError(Exception):
    def __init__(self, message, *, validation_error, raw_response):
        ...
        self.validation_error = validation_error
        self.raw_response = raw_response
```

It carries the raw `anthropic` `Message` object and the Pydantic
`ValidationError` (or `None`, which specifically distinguishes "no tool_use
block at all" from "produced one that didn't validate" — two different
failure shapes worth telling apart) as real, structured attributes, not a
formatted string. When Stage 5 builds real retry-with-feedback, it has
everything it needs to construct a corrective `tool_result` without this
function's contract changing at all.

### `aws_profile` as an explicit parameter, not ambient shell state

```python
def structured_output(..., aws_profile: str = _DEFAULT_AWS_PROFILE) -> T:
```

This is the third time this exact category of bug hit this project's setup
in this session — first the post-commit hook's `CLAUDE_CODE_OAUTH_TOKEN`
(silently unavailable because the token lived in one terminal's exported
environment and the hook ran in a separate shell context), then this same AWS
credential issue, described fully in section 5 below. The user named the
generalizable lesson directly: anything depending on ambient shell state
breaks the moment the code runs somewhere other than the one terminal it was
last tested in. Making `aws_profile` an explicit parameter with a sensible
default means the dependency travels with the function, in version control,
rather than living in whoever's terminal happens to be open.

---

## 5. The debugging narrative — three layered failures, each closed with real evidence

This component needed three genuinely distinct problems solved, in sequence,
before a live call worked. None of the three fixes were reachable by
plausible-sounding guessing — both the profile name and the model ID that
finally worked are specific to this account and were looked up directly, not
recalled from general knowledge.

**Failure 1 — credentials unresolvable.** The first live attempt raised:

```
RuntimeError: could not resolve credentials from session
```

raised from deep inside `anthropic/lib/bedrock/_auth.py`. Rather than guess
at a fix, that file's actual source was read directly. It showed
`AnthropicBedrock` builds its own `boto3.Session(profile_name=profile,
region_name=region, aws_access_key_id=aws_access_key, ...)` internally, using
only whatever was explicitly passed to the client constructor — and since
only `aws_region` had been passed, `profile` stayed `None`, meaning the
internal session only ever checked the `"default"` AWS profile.

**Isolating the layer.** To confirm this was a genuine credential-resolution
problem and not a bug specific to the `anthropic` library, the user ran a
minimal boto3-only diagnostic reproducing the exact same session-construction
call `_auth.py` uses, with no `anthropic` import at all:
`boto3.Session(region_name="us-east-1").get_credentials()`. It returned
`None` too — confirming the failure was genuinely "no credentials reachable
in this shell," not an SDK defect. (One piece of real friction along the way:
a stray `zsh: command not found: $` shell-escaping artifact from how the
diagnostic snippet was quoted when pasted — harmless, the substantive Python
output printed correctly regardless, but worth keeping as an honest record
that this troubleshooting wasn't a single clean pass.)

**Failure 2 — the named profile.** `~/.aws/` had been checked once already,
earlier in this same investigation, and didn't exist at all at that point.
Checking again later found it now existed, with `config`/`credentials` files
and a `cli/cache` subdirectory. Rather than assume the new files fixed
things, the identical boto3 diagnostic was re-run — it still returned
`False`. This is the same discipline already established with the Component
5 regression test: never assume a state change fixed something without
re-verifying the specific claim, even when a change appearing to exist makes
success seem obvious. The actual explanation only surfaced by grepping the
profile *section names* in both files (never their key values):
`grep '^\[' ~/.aws/config` returned `[profile bedrock]`, and the credentials
file returned `[bedrock]` — not `[default]`. The credentials were real and
present the whole time; they just lived under a name boto3's implicit
default-profile lookup never checks.

**Failure 3 — on-demand invocation not supported.** With `aws_profile`
added, the next live attempt got *past* the credentials layer entirely — a
genuine Bedrock API response, not an auth error — but hit a new, distinct
failure:

```
anthropic.BadRequestError: Error code: 400 - {'message': 'Invocation of
model ID anthropic.claude-sonnet-4-6 with on-demand throughput isn't
supported. Retry your request with the ID or ARN of an inference profile
that contains this model.'}
```

This is a real, Bedrock-specific constraint: certain Claude models are only
invokable through a "cross-region inference profile" construct, not directly
by their bare foundation-model ID. Rather than guess at the naming pattern
from general knowledge (a plausible-looking but unverified guess like
prefixing `"us."` onto the model ID), the correct profile ID was queried
directly against the real account via `boto3`'s Bedrock client,
`list_inference_profiles()`, filtered for `"sonnet-4-6"`. Two real candidates
came back: `us.anthropic.claude-sonnet-4-6` (a US cross-region profile
spanning `us-east-1`/`us-east-2`/`us-west-2`) and
`global.anthropic.claude-sonnet-4-6`. The region-scoped one was chosen to
match the account's actual `us-east-1` setup.

**Success.** With the corrected model ID, the identical test call finally
succeeded — a genuine `ParamBounds(param_name='moving_average_length',
min_value=2.0, max_value=200.0)` came back from a real live call, tool-use
forcing and Pydantic validation working together end to end for the first
time. The full 17-test regression suite was confirmed unchanged immediately
after.

The throughline across all three failures: each one was closed by getting a
specific, checkable piece of evidence — reading the actual auth source code,
reproducing the failure with a minimal isolated repro, grepping structural
file contents (never secret values), querying the live account directly —
rather than by pattern-matching to something that sounded right and hoping
it would work.

---

## 6. How the verification gate was satisfied

Two categories of verification happened here, and they matter differently.

**Offline verification** (no network call): the SDK's typed response models
were inspected directly — `ToolUseBlock`'s exact field names (`input`,
`name`, `type`, confirmed via `model_fields`), `Message.content`'s presence,
tool-schema construction from a representative Pydantic model, and
`StructuredOutputError`'s attribute wiring using a hand-constructed fake
`Message`. This is the same category of check used throughout Stage 3 and
it's necessary, but for this component specifically, it was not *sufficient*.

**Live verification**: a real end-to-end call through `AnthropicBedrock`,
tool-use forcing, and Pydantic validation, producing a genuine, sensible
`ParamBounds` object. This is the actual gate for this component, more so
than for any earlier one in Stage 3, for a structural reason: this is the
first component whose correctness depends on facts that live entirely
outside this codebase — one specific AWS account's profile configuration,
and Bedrock's own per-model invocation constraints. No amount of offline type
inspection or synthetic testing could have caught either the profile-name
mismatch or the inference-profile requirement, because neither one is a
property of the code at all.

**What remains unverified.** `StructuredOutputError`'s actual failure-path
code — the branches handling a missing `tool_use` block or a genuine
`ValidationError` — has only ever been exercised with a synthetic, hand-built
fake `Message`, never with a real malformed response from Claude. Tool-use
forcing is reliable but not infallible; the first time a real response
actually fails to validate, this is the code path that will run for the
first time under real conditions.

---

## 7. Interview defense

**Q: Why not just tell the user to export `AWS_PROFILE=bedrock` instead of
adding a function parameter?**

A: Because that's exactly the failure mode that already broke this session's
setup twice before this component even started — a credential or config
value living in one terminal's environment and silently not being there when
the same code runs in a different shell. The fix needs to live in the code
that depends on it, as a parameter with a sensible default, not in whichever
terminal happens to be open when someone runs this function next.

**Q: Why forced tool-use instead of asking Claude to return JSON in its
response text and parsing that?**

A: Free-text JSON generation is genuinely less reliable — models can wrap
output in markdown code fences, add explanatory prose before or after the
JSON, or produce subtly malformed structures that a naive `json.loads()`
chokes on. Tool-use forcing constrains the response structurally: Claude is
calling a defined tool whose input schema *is* `response_model`'s own schema,
which is both more reliable on the first attempt and — since it was
confirmed empirically that `AnthropicBedrock` and `Anthropic` share the exact
same `Messages` resource class — costs nothing in terms of provider
portability.

**Q (hard): You corrected the plan's stated provider from direct Anthropic
API to Bedrock based on the user's stated memory of a decision that
`architecture.md` itself only states permissively — "Bedrock or the direct
Anthropic API" — not as an exclusive mandate. What if that memory of "the
decision was Bedrock, for cost reasons" was itself imprecise, and an entire
Bedrock integration got built and debugged — three real rounds of
troubleshooting — against a decision that wasn't actually as settled as
stated?**

A: The honest process was: re-read `architecture.md`'s exact wording before
proceeding, rather than trusting memory of it in either direction. What it
actually says — pointing "at Bedrock (AWS credits) or the direct Anthropic
API," with the provider choice framed as "a one-line change" — doesn't
mandate Bedrock, but it also doesn't contradict the user's stated intent.
That's the check that actually matters here: not whether the document proves
the user's memory correct in every detail, but whether proceeding on their
stated instruction would silently contradict something already settled in
writing. It wouldn't have. Once that's confirmed, the user's explicit,
current instruction is authoritative — they own this project's actual
decisions, including what an earlier, more loosely-worded decision was
actually intended to mean, and that's not something to keep relitigating
once they've stated it plainly. Verifying a document's exact wording is
about not silently contradicting or silently exceeding what's written down;
it isn't a mechanism for overruling the person whose intent the document is
supposed to capture in the first place.

**Honest weakness:** the untested failure path noted in section 6.
`structured_output`'s contract is "a valid instance, or an exception" — a
defect here would most likely surface as a loud crash, which is the
preferable failure mode by this project's own standards, not a silent wrong
value. But the specific code that constructs and raises
`StructuredOutputError` on a genuine validation failure has never actually
run against a real bad response. If there's a bug in *that* code rather than
in the validation logic it's reporting on, it will surface for the first
time the moment Component 8 gets its first real malformed response from
Claude — not before.

---

## 8. What comes next and why

Component 8 (plan-item 7) — extended indicator generation and verification —
is the actual first real *use* of this module: `structured_output` proposing
parameter bounds for pandas-ta indicators outside the hand-verified core set.
This is also where the amended "Stages 1–3 use no LLM, except for exactly
this one bounded, offline case" rule gets its first real exercise beyond
infrastructure — everything built in this component has been proving the
plumbing works, not yet using it for anything.

If this component were subtly wrong in a way nobody caught, the most likely
symptom, given its "valid instance or exception" contract, is a loud failure
rather than a silently wrong value — consistent with this project's general
posture that a crash is a better failure mode than a plausible-looking wrong
answer. The sharper residual risk is the specific untested path flagged in
section 6: if the error-construction code itself has a defect, independent of
whatever Claude actually returned, that defect is invisible until Component
8 hands this module its first genuinely malformed response — which could be
the very first time this module is used for something that matters.
