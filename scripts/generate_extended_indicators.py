"""Generate candidate extended-tier indicator registry entries.

One-time, offline, build-time script (see CLAUDE.md's amendment to "Stages 1-3 use
no LLM" and docs/architecture.md section 7 provider abstraction). Everything code can
determine deterministically is determined by code: which pandas-ta functions exist,
what OHLCV inputs each requires, which of its parameters are numeric and tunable,
what its output columns are called. The LLM's only job is proposing (min, max) bounds
for parameters code has already identified as numeric and tunable, plus an optional
cross-parameter ordering constraint (the MACD fast<slow pattern, generalized).

Output: src/backtester/extended_indicators.py, every entry verified=False. Nothing
here is trusted until scripts/verify_extended_indicators.py executes it for real.

Run: .venv/bin/python scripts/generate_extended_indicators.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

import pandas as pd
import pandas_ta as ta
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _extended_codegen import make_synthetic_ohlcv, param_midpoint, render_extended_indicators_module  # noqa: E402

from backtester.indicators import (  # noqa: E402
    CORE_INDICATORS,
    _derive_column_prefixes,
    _extended,
    _infer_inputs,
    _numeric_tunable_params,
    normalize_params,
)
from llm_client import StructuredOutputError, structured_output  # noqa: E402

CHUNK_SIZE = 20
# Inference profile ID, not the bare foundation-model ID -- Bedrock rejects on-demand
# invocation by bare ID (same lesson llm_client's own _DEFAULT_MODEL docstring
# documents for Sonnet). Looked up via `aws bedrock list-inference-profiles`, not
# guessed from the region-prefix pattern.
BOUNDS_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


# ---------------------------------------------------------------------------
# Structured LLM response shape -- bounds proposals only, batched
# ---------------------------------------------------------------------------

class ParamBounds(BaseModel):
    param: str
    min: float
    max: float


class CrossCheckProposal(BaseModel):
    type: Literal["less_than", "greater_than", "not_equal"]
    left: str
    right: str


class IndicatorBoundsProposal(BaseModel):
    indicator: str
    bounds: list[ParamBounds]
    cross_check: CrossCheckProposal | None = None


class ExtendedIndicatorBoundsBatch(BaseModel):
    proposals: list[IndicatorBoundsProposal]


# ---------------------------------------------------------------------------
# Phase 1: deterministic introspection
# ---------------------------------------------------------------------------

def _all_pandas_ta_names() -> list[str]:
    return sorted({name for names in ta.Category.values() for name in names})


def _core_fn_names() -> set[str]:
    return {spec.fn.__name__ for spec in CORE_INDICATORS.values()}


class Candidate:
    def __init__(self, name: str, fn) -> None:
        self.name = name
        self.fn = fn
        self.inputs: tuple[str, ...] = ()
        self.first_result = None
        self.numeric_params: tuple[str, ...] = ()


def classify_candidates(data: pd.DataFrame) -> tuple[list[Candidate], list[Candidate], dict[str, str]]:
    """Returns (no_numeric, needs_llm, skipped) -- skipped maps name -> reason."""
    core_names = _core_fn_names()
    no_numeric: list[Candidate] = []
    needs_llm: list[Candidate] = []
    skipped: dict[str, str] = {}

    for name in _all_pandas_ta_names():
        if name in core_names:
            continue
        fn = getattr(ta, name, None)
        if fn is None:
            skipped[name] = "not found on pandas_ta module"
            continue

        inputs = _infer_inputs(fn)
        if not inputs:
            skipped[name] = "no OHLCV input (meta-indicator over other series, e.g. long_run/short_run)"
            continue

        args = [data[field] for field in inputs]
        try:
            result = fn(*args)
        except Exception as e:  # noqa: BLE001 -- deliberately broad, this is a probe
            skipped[name] = f"execution raised: {e!r}"
            continue
        if result is None:
            skipped[name] = "returned None"
            continue
        if not isinstance(result, (pd.Series, pd.DataFrame)):
            # Confirmed for real: ta.ichimoku returns tuple[DataFrame, DataFrame]
            # (its forward-looking "cloud" span genuinely extends past the input
            # data, a display quirk unique to this indicator) -- not a shape this
            # registry's column_prefix/Series assumptions can express at all.
            skipped[name] = f"unsupported return type {type(result).__name__} (expected Series or DataFrame)"
            continue

        cand = Candidate(name, fn)
        cand.inputs = inputs
        cand.first_result = result
        cand.numeric_params = _numeric_tunable_params(fn)

        if cand.numeric_params:
            needs_llm.append(cand)
        else:
            no_numeric.append(cand)

    return no_numeric, needs_llm, skipped


# ---------------------------------------------------------------------------
# Phase 2: batched LLM bounds proposals, each chunk independently fault-tolerant
# ---------------------------------------------------------------------------

def _prompt_for_chunk(chunk: list[Candidate]) -> str:
    lines = [
        "You are proposing safe (min, max) parameter bounds for technical analysis "
        "indicator functions from the pandas-ta library. For each indicator below, "
        "propose numeric bounds for every listed parameter, based on how that "
        "parameter is conventionally used (e.g. a 'length' or lookback parameter is "
        "usually a small positive integer; a 'scalar' or multiplier is often near 1-100).",
        "If two parameters are conventionally ordered relative to each other (e.g. a "
        "'fast' period must be less than a 'slow' period, the way MACD's fast/slow "
        "periods work), propose a cross_check for that indicator. Only propose a "
        "cross_check when you are confident the constraint is real -- omit it otherwise.",
        "",
    ]
    for cand in chunk:
        doc = (ta_first_doc_line(cand.fn) or "").strip()
        lines.append(f"- indicator={cand.name!r}, params={list(cand.numeric_params)!r}" + (f", docs: {doc}" if doc else ""))
    return "\n".join(lines)


def ta_first_doc_line(fn) -> str | None:
    doc = fn.__doc__
    if not doc:
        return None
    for line in doc.strip().splitlines():
        line = line.strip()
        if line:
            return line[:150]
    return None


def propose_bounds(needs_llm: list[Candidate]) -> dict[str, IndicatorBoundsProposal]:
    """Batched, chunked structured_output calls. Each chunk's failure is isolated --
    logged and skipped, never aborting the remaining chunks (see plan: one bad chunk
    must not silently discard every successful chunk before it)."""
    proposals: dict[str, IndicatorBoundsProposal] = {}
    chunks = [needs_llm[i : i + CHUNK_SIZE] for i in range(0, len(needs_llm), CHUNK_SIZE)]

    for i, chunk in enumerate(chunks):
        try:
            batch = structured_output(
                _prompt_for_chunk(chunk),
                ExtendedIndicatorBoundsBatch,
                model=BOUNDS_MODEL,
                max_tokens=8192,
            )
        except StructuredOutputError as e:
            print(f"  [chunk {i + 1}/{len(chunks)}] SKIPPED -- {e}")
            continue
        except Exception as e:  # noqa: BLE001 -- network/transport errors etc; isolate per chunk
            print(f"  [chunk {i + 1}/{len(chunks)}] SKIPPED -- unexpected error: {e!r}")
            continue

        for proposal in batch.proposals:
            proposals[proposal.indicator] = proposal
        print(f"  [chunk {i + 1}/{len(chunks)}] got {len(batch.proposals)} proposals")

    return proposals


# ---------------------------------------------------------------------------
# Phase 3: finalize -- accept only valid bounds, derive column prefixes, build specs
# ---------------------------------------------------------------------------

def _accepted_bounds(cand: Candidate, proposal: IndicatorBoundsProposal | None) -> dict[str, tuple[float, float]]:
    """Only params the LLM proposed a valid (min < max) bound for, and only params
    code already identified as numeric-tunable for this function, make it through.
    A numeric param the LLM never covers simply never enters `params` -- it stays
    fixed at pandas-ta's own internal default, which is a conservative, not broken,
    fallback (schema.py never requires every numeric param to be exposed)."""
    if proposal is None:
        return {}
    accepted = {}
    for b in proposal.bounds:
        if b.param not in cand.numeric_params:
            continue  # LLM named a param that isn't actually tunable on this function
        if not (b.min < b.max):
            continue  # degenerate range, reject rather than silently accept
        accepted[b.param] = (b.min, b.max)
    return accepted


def _accepted_cross_check(accepted: dict, proposal: IndicatorBoundsProposal | None) -> dict | None:
    if proposal is None or proposal.cross_check is None:
        return None
    cc = proposal.cross_check
    if cc.left not in accepted or cc.right not in accepted:
        return None  # can't reference a param that didn't survive bounds acceptance
    return {"type": cc.type, "left": cc.left, "right": cc.right}


def build_specs(
    data: pd.DataFrame,
    no_numeric: list[Candidate],
    needs_llm: list[Candidate],
    proposals: dict[str, IndicatorBoundsProposal],
) -> tuple[dict, dict[str, str]]:
    specs: dict = {}
    # Seeded with core registry names, not just started empty: a derived extended
    # name can collide with a hand-verified CORE entry, not only with another
    # extended candidate -- confirmed for real (aobv's output includes a raw "OBV"
    # column, colliding with the core OBV entry). Catching that here means
    # registry.py's own collision check never has anything to catch; it stays as a
    # second, independent line of defense rather than the only one.
    used_names: dict[str, str] = {name: "core" for name in CORE_INDICATORS}
    skipped: dict[str, str] = {}

    def register(cand: Candidate, params: dict, cross_check: dict | None, *results):
        try:
            prefixes = _derive_column_prefixes(*results)
        except ValueError as e:
            # A function whose output COLUMN COUNT itself depends on parameter
            # values (confirmed for real: aobv's fast/slow combination collapses
            # or splits an OBV-EMA column depending on the values chosen) can't be
            # safely registered via a fixed set of column_prefix entries -- there's
            # no single schema that's correct across the whole declared bounds
            # range. Skip the whole indicator rather than register something that
            # would only be structurally valid for some parameter choices.
            skipped[cand.name] = f"output column count varies with parameter values, not safely registrable: {e}"
            return
        if prefixes is None:
            named = [(cand.name.upper(), None, cand.name)]
        else:
            source_cols = list(results[0].columns)
            named = [
                (prefix.rstrip("_").upper() or cand.name.upper(), prefix, col)
                for prefix, col in zip(prefixes, source_cols)
            ]

        for derived_name, prefix, source_col in named:
            owner = used_names.get(derived_name)
            if owner is not None:
                if owner == cand.name:
                    # Real, not a bug: some pandas-ta functions embed the varying
                    # param BEFORE the letter suffix that distinguishes sibling
                    # output columns (e.g. tos_stdevall's TOS_STDEVALL_<length>_L_1
                    # vs _U_1 -- the literal text up to the digit is identical for
                    # every column). column_prefix is a plain str.startswith prefix,
                    # so no value-independent prefix can separate columns whose
                    # distinguishing part comes after the digit, not before it.
                    reason = (
                        f"output column not prefix-distinguishable from a sibling "
                        f"column of the same function (both reduce to derived name "
                        f"{derived_name!r} -- the varying parameter is embedded "
                        f"before, not after, the distinguishing suffix)"
                    )
                else:
                    reason = f"derived name {derived_name!r} collides with an entry already registered from {owner!r}"
                skipped[f"{cand.name}.{source_col}"] = reason
                continue
            used_names[derived_name] = cand.name
            specs[derived_name] = _extended(cand.fn, params=params, column_prefix=prefix, cross_check=cross_check)

    for cand in no_numeric:
        register(cand, {}, None, cand.first_result)

    for cand in needs_llm:
        proposal = proposals.get(cand.name)
        if proposal is None:
            skipped[cand.name] = "no LLM proposal (chunk failed or indicator omitted from response)"
            continue
        accepted = _accepted_bounds(cand, proposal)
        cross_check = _accepted_cross_check(accepted, proposal)

        if not accepted:
            register(cand, {}, None, cand.first_result)
            continue

        args = [data[field] for field in cand.inputs]
        # Three sample points, not two: a third point (the bounds' midpoint)
        # breaks the coincidental-digit-prefix collision a 2-point diff can fall
        # for -- see indicators._derive_column_prefixes's docstring (confirmed for
        # real on kc's length bounds (1, 100), which share a leading "1" digit).
        try:
            result_min = cand.fn(*args, **normalize_params({p: lo for p, (lo, hi) in accepted.items()}))
            result_mid = cand.fn(*args, **normalize_params({p: param_midpoint(b) for p, b in accepted.items()}))
            result_max = cand.fn(*args, **normalize_params({p: hi for p, (lo, hi) in accepted.items()}))
        except Exception as e:  # noqa: BLE001 -- probe execution at proposed bounds
            skipped[cand.name] = f"execution at proposed bounds raised: {e!r}"
            continue
        if result_min is None or result_mid is None or result_max is None:
            skipped[cand.name] = "execution at proposed bounds returned None"
            continue

        register(cand, accepted, cross_check, result_min, result_mid, result_max)

    return specs, skipped


def main() -> None:
    data = make_synthetic_ohlcv()

    print("Classifying pandas-ta functions...")
    no_numeric, needs_llm, skipped_classify = classify_candidates(data)
    print(f"  no numeric params (direct register): {len(no_numeric)}")
    print(f"  needs LLM bounds proposal: {len(needs_llm)}")
    print(f"  skipped at classification: {len(skipped_classify)}")

    print(f"Proposing bounds for {len(needs_llm)} indicators in chunks of {CHUNK_SIZE}...")
    proposals = propose_bounds(needs_llm)
    print(f"  got proposals for {len(proposals)}/{len(needs_llm)} indicators")

    print("Building final specs...")
    specs, skipped_build = build_specs(data, no_numeric, needs_llm, proposals)

    out_path = Path(__file__).resolve().parent.parent / "src" / "backtester" / "extended_indicators.py"
    out_path.write_text(render_extended_indicators_module(specs))

    all_skipped = {**skipped_classify, **skipped_build}
    print()
    print(f"Wrote {len(specs)} candidate entries to {out_path}")
    print(f"Skipped {len(all_skipped)} total:")
    for name, reason in sorted(all_skipped.items()):
        print(f"  {name}: {reason}")


if __name__ == "__main__":
    main()
