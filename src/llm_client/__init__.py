"""Minimal LLM abstraction — the project's only entry point for calling Claude.

Single call, single validation attempt, raise on failure. No retry loop —
and, having now been decided in Stage 5, this is permanent rather than
pending.

The original note here said this module "gains retries in Stage 5", because
what retry should mean (same prompt again? feedback fed back to the model?
capped by what budget?) depended on loop guardrails that did not exist yet.
Those guardrails now exist, and the answer turned out to be that retry does
NOT belong in this module. Three callers deliberately do not retry, each for
a documented reason: agentic_core/charter.py is human-mediated, so re-running
the script IS the retry; agentic_core/hypothesis.py raises
DuplicateHypothesisError specifically so the caller decides; and
agentic_core/study_design.py raises InsufficientHistoryError on the same
reasoning. A general retry underneath structured_output would silently
override all three.

Retry therefore lives in the one caller that runs unattended with a budget:
agentic_core/loop_graph.py's decide_next_action, which retries up to
MAX_DECISION_ATTEMPTS with the validation error fed back to the model, and
charges every attempt against the loop's own step budget. This function's
no-retry contract is what makes that possible — a caller cannot implement a
budgeted retry on top of a function that has already silently retried.

StructuredOutputError carries the raw response and the Pydantic validation
error, which is exactly what that retry logic reads to build its feedback.

Built against AWS Bedrock (AnthropicBedrock), not the direct Anthropic API —
this project's LLM usage is funded by AWS credits (docs/architecture.md,
cost discipline), not a direct Anthropic API key.

_DEFAULT_MODEL is a cross-region inference profile ID ("us.anthropic..."), not
the bare foundation-model ID ("anthropic.claude-sonnet-4-6") — confirmed by an
actual failed call: Bedrock rejects on-demand invocation of this model by its
bare ID ("Invocation of model ID ... with on-demand throughput isn't
supported"), and the correct profile ID was looked up directly via
list_inference_profiles() against the real account rather than guessed at from
the region-prefix pattern alone.
"""

from __future__ import annotations

from typing import TypeVar

from anthropic import AnthropicBedrock
from anthropic.types import Message
from langsmith import traceable
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"  # inference profile ID, not bare model ID — see module docstring
_DEFAULT_REGION = "us-east-1"
_DEFAULT_AWS_PROFILE = "bedrock"


class StructuredOutputError(Exception):
    """Claude's response did not yield a validated response_model.

    validation_error is None when the response contained no tool_use block at
    all (a different failure than "produced one that didn't validate").
    raw_response is always the full Message, so a caller can inspect exactly
    what Claude actually said.
    """

    def __init__(self, message: str, *, validation_error: ValidationError | None, raw_response: Message) -> None:
        super().__init__(message)
        self.validation_error = validation_error
        self.raw_response = raw_response


@traceable(run_type="llm", name="structured_output")
def structured_output(
    prompt: str,
    response_model: type[T],
    model: str = _DEFAULT_MODEL,
    max_tokens: int = 4096,
    aws_region: str = _DEFAULT_REGION,
    aws_profile: str = _DEFAULT_AWS_PROFILE,
) -> T:
    """Call Claude once; parse and validate the response into response_model.

    Uses forced tool-use, not free-text JSON parsing: Claude is constrained to
    produce arguments matching response_model's own JSON schema via a tool
    call, which is the reliable mechanism for structured output on the first
    attempt — this is a single-shot correctness choice, independent of the
    no-retry decision above.

    aws_profile is an explicit parameter, not left to the AWS_PROFILE
    environment variable or boto3's "default" profile fallback. Ambient shell
    state is exactly what broke twice already in this project's setup (the
    post-commit hook's CLAUDE_CODE_OAUTH_TOKEN, and this same AWS profile the
    first time this function was tested) — both times because the credential
    a call depended on lived in one terminal's environment and the code ran in
    another. Naming the profile in code means the dependency travels with the
    function, not with whoever's shell happens to be open.

    Raises StructuredOutputError if the response has no tool_use block, or if
    the tool_use input fails Pydantic validation. Never returns a partially-
    valid or best-effort object — the contract is: a valid response_model
    instance, or an exception. Nothing unvalidated proceeds past this function.
    """
    tool_name = f"emit_{response_model.__name__.lower()}"
    client = AnthropicBedrock(aws_region=aws_region, aws_profile=aws_profile)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
        tools=[
            {
                "name": tool_name,
                "description": f"Emit a validated {response_model.__name__}.",
                "input_schema": response_model.model_json_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": tool_name},
    )

    tool_use_block = next((block for block in response.content if block.type == "tool_use"), None)
    if tool_use_block is None:
        raise StructuredOutputError(
            f"Claude's response contained no tool_use block for {tool_name!r}",
            validation_error=None,
            raw_response=response,
        )

    try:
        return response_model.model_validate(tool_use_block.input)
    except ValidationError as e:
        raise StructuredOutputError(
            f"{response_model.__name__} failed validation: {e}",
            validation_error=e,
            raw_response=response,
        ) from e
