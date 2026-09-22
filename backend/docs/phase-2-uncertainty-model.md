# Phase 2E — Investigation Uncertainty Engine

**This is a project-development uncertainty model. It is not an official
HHGoa bank policy. It is not a fraud probability. It is not a
replacement for analyst judgment.**

Status: **COMPLETE**. Offline: 31/31 PASS. Live: 11/11 PASS.

## 1. What this phase adds

```
InvestigationSnapshot (Phase 2D)
        |
        v
UncertaintyEngine.assess()     <- pure computation, no TigerGraph, no LLM
        |
        v
UncertaintyAssessment
```

`UncertaintyEngine` (`app/uncertainty/engine.py`) consumes an
`InvestigationSnapshot` and produces an `UncertaintyAssessment`
(`app/uncertainty/models.py`). It answers five questions about the
**investigation**, never about the transaction:

1. How complete is the investigation? (`evidence_coverage`)
2. How reliable is the evidence obtained? (`signal_quality`)
3. Does the evidence contradict itself? (`signal_conflict`, `conflicting_evidence`)
4. Is the underlying data actually populated? (`data_completeness`)
5. Is there enough to hand to the next stage? (`sufficient_for_next_stage`)

It never asks "is this fraud?" and never asks an LLM "how confident are
you?" — there is no LLM anywhere in this package.

## 2. Concepts kept strictly separate

| Concept | Where it lives | This phase touches it? |
|---|---|---|
| Dataset label (`isFraud`) | `EvidenceBundle.dataset_risk_score` (Phase 2B) | **Never read** |
| Investigation evidence | `Evidence` (Phase 2B) | Read, not modified |
| Evidence quality | `Evidence.quality` (Phase 2B) | Aggregated |
| Evidence coverage | *new this phase* | Computed |
| Signal conflict | *new this phase* | Computed |
| Data completeness | *new this phase* | Computed |
| Investigation uncertainty | *new this phase* | Computed |
| Agent confidence | — | **Not implemented** |
| Fraud probability | — | **Not implemented** |

`UncertaintyAssessment` has no `fraud_probability`, `final_fraud_score`,
`agent_confidence`, or `fraud_verdict` field — enforced by
`TestNoVerdictOrLLM::test_no_fraud_field_exists_on_the_assessment_model`.

## 3. Architectural boundary — no TigerGraph, no LLM

`app/uncertainty/engine.py` and `models.py` never import `app.tigergraph`
anything. This is not just a docstring claim — verified by parsing both
files' own import statements with `ast` and asserting no import targets
`app.tigergraph.*`
(`TestArchitecturalBoundary::test_module_never_imports_tigergraph_directly`),
and by running `assess()` against a hand-built, synthetic
`InvestigationSnapshot` with no `TigerGraphClient` anywhere in the call
graph (`test_assess_runs_on_a_synthetic_snapshot_with_no_tigergraph_client_anywhere`).
A separate static check confirms the source never contains `"openai"`,
`"langgraph"`, or `"import random"`
(`test_no_llm_or_openai_reference_anywhere_in_the_package`).

Live measurement (Step 29): `UncertaintyEngine.assess()` averages
**0.105ms** per call over 20 runs against a real snapshot — the
investigation that produced that snapshot took multiple seconds. Pure
computation, no additional TigerGraph queries.

## 4. Evidence coverage

```python
evidence_coverage = (planned sources that completed SUCCESS or EMPTY) / 6
```

The six planned sources are exactly `INVESTIGATION_PLAN`
(`app/investigation/snapshot.py`, Phase 2D). A tool counts as covered if
it ran and ended `SUCCESS` **or** `EMPTY` — both are a completed query;
`EMPTY` just means it found nothing, which is still information (Phase
2E §7). A tool counts as **not** covered if it ended `ERROR` or never ran
at all (context failed first, or an overall investigation timeout
skipped it). No weights: every planned source counts equally, because
this phase has no principled basis yet to weight one evidence area over
another for *coverage* purposes specifically (as opposed to *quality*,
which is weighted by the categorical rating below).

`0.0` = none of the planned evidence was obtained. `1.0` = all six
sources completed. This is a coverage metric, **not** a probability of
correctness.

## 5. Signal quality

```python
_QUALITY_SCORE = {LOW: 0.33, MEDIUM: 0.66, HIGH: 1.0}
signal_quality = average(_QUALITY_SCORE[e.quality] for e in evidence
                          if e.status == SUCCESS and e.quality in _QUALITY_SCORE)
# None if no such item exists
```

This is an **internal evidence-quality normalization only**. It is not a
bank score, not a fraud probability, not official HHGoa policy — the
categorical `SignalQuality` value (Phase 2B, unchanged) remains on every
`Evidence` item and is what actually carries meaning; this float exists
only so multiple quality ratings can be averaged on a common scale.

**Only `SUCCESS`-status items with an actual `LOW`/`MEDIUM`/`HIGH`
rating are included.** `UNKNOWN`-quality items are excluded from the
average, not scored as `0.0`. This matters concretely:
`transaction_context` is *always* `UNKNOWN` quality by Phase 2B design
(it's not a relationship signal), and any `EMPTY`/`ERROR` item's quality
is also forced to `UNKNOWN`. If `UNKNOWN` were scored as `0.0`, the
ever-present context item alone would silently drag every single
assessment's quality down regardless of the real evidence obtained —
verified directly: `test_unknown_quality_items_never_pull_down_the_average`
constructs a HIGH-quality item alongside an UNKNOWN one and asserts the
average stays `1.0`, not `0.5`.

**Relationship count is never used as a quality proxy.** The known,
documented finding — shared `Address` can link to hundreds of
transactions yet remains a `LOW`-quality signal — is preserved exactly:
`_signal_quality` reads only the categorical `quality` field, never
`related_transaction_count`. Verified live: on the real fixture
transaction (Address=312 related, `LOW`), the aggregate `signal_quality`
stays below the `MEDIUM` ceiling (0.66), not inflated by the large count
(`test_address_low_quality_is_reflected_in_the_aggregate`).

## 6. Data completeness

```python
_CONTEXT_REQUIRED_FIELDS = ("is_fraud_label", "transaction_amt", "product_cd")
data_completeness = (fields populated) / 3   # None if transaction_context did not succeed
```

This is deliberately **narrower** than `evidence_coverage` and computes
a genuinely different thing. `transaction_context`'s three metrics are
the only place in the current snapshot where this engine has real,
per-field nullability visibility — they come straight from
`TransactionContext.attributes.get(...)` (Phase 2A) and can be
legitimately `None` if the underlying TigerGraph vertex has a null
attribute, which is a real data-quality fact, not a query failure. The
other five tools' metrics are always well-formed by construction
whenever their status is `SUCCESS`/`EMPTY` (Phase 2B's normalizers only
populate a metric when there is something to report), so this engine
does not manufacture a false-precision completeness statistic for them.
If a broader completeness metric becomes useful later, extending this
function is a Phase 2F+ concern, not something invented here without a
real basis (Phase 2E §9's explicit instruction).

A null field here becomes its own `MissingEvidence` entry with reason
`DATA_MISSING` (distinct from `QUERY_ERROR` — the tool succeeded, but
this specific value was absent).

## 7. Signal conflict

Four grounded, deterministic detection rules — **no conflict is ever
manufactured merely because two counts differ** (Phase 2E §13). Each
rule fires **at most once** per assessment (aggregating every affected
evidence item into a single conflict record), so the maximum possible
severity-weighted total is fixed and known:

| Rule | `ConflictType` | Severity | Trigger |
|---|---|---|---|
| Context vs. dedicated query disagree on whether an entity is linked at all | `SIGNAL_DISAGREEMENT` | HIGH | `transaction_context.has_card` (etc.) disagrees with whether `find_shared_card_activity` found a linked entity — two supposedly-consistent facts about the same transaction should never differ |
| Network breakdown vs. dedicated query counts disagree | `DATA_INCONSISTENCY` | HIGH | `network_pattern.related_by_entity_type[X]` ≠ the corresponding dedicated shared-entity `related_transaction_count` — both derive from the same graph edges and must match exactly (the same invariant Phase 2A/2B/2C/2D already assert in their own tests) |
| A large apparent network is driven by low-quality linkage | `QUALITY_DISPARITY` | MEDIUM | `network_pattern` is `SUCCESS`, its own `quality` is `LOW` (≥75% of related transactions reached only via Address/EmailDomain — Phase 2B's own `network_pattern_quality()`), and `total_related_not_deduplicated` ≥ 10 (configurable `quality_disparity_min_related`) |
| Some evidence areas are missing while others succeeded | `MISSING_CONTEXT` | LOW | At least one non-context tool is `ERROR`/not-run while at least one other is `SUCCESS` with findings — the picture is uneven, not necessarily wrong |

`ConflictType.SIGNAL_DISAGREEMENT` is also listed to cover a genuinely
different case that never fires from the same rule as
`DATA_INCONSISTENCY`: it is about two facts disagreeing on **existence**
(is there a card at all?), while `DATA_INCONSISTENCY` is about two facts
disagreeing on a **count** for an entity both already agree exists.

```python
_SEVERITY_WEIGHT = {LOW: 0.25, MEDIUM: 0.5, HIGH: 1.0}
signal_conflict = min(1.0, sum(weight of each detected conflict) / 4.0)
```

`4.0` is the true maximum (4 rules × HIGH=1.0 each, each firing at most
once) — not an arbitrary cap. `0.0` when no conflicts are detected.

## 8. Overall uncertainty

```python
overall_uncertainty = average(
    1 - evidence_coverage,
    signal_conflict,
    1 - signal_quality,      # only if signal_quality is not None
    1 - data_completeness,   # only if data_completeness is not None
)
```

Only the factors that are **actually available** are averaged — an
unavailable `signal_quality`/`data_completeness` is excluded from the
average, never silently substituted with `0` (Phase 2E §21).
`evidence_coverage` and `signal_conflict` are always computable (even a
snapshot with zero evidence has a well-defined `0.0` coverage and a
trivial `0.0` conflict score), so under normal circumstances this always
produces a number in `[0.0, 1.0]`.

**Exception**: when `InvestigationSnapshot.status == FAILED` (transaction
context itself could not be established), `overall_uncertainty` is
explicitly `None` (`UncertaintyLevel.UNKNOWN`), not a formula-derived
number. In that state, `signal_conflict` would be a vacuous `0.0` (there
is no evidence to check for contradictions), so blending it into an
average would understate how little is actually known — Phase 2E §22
explicitly prefers `UNKNOWN` over inventing a number when a required
precondition (here: even knowing the transaction exists) was not met.

## 9. Uncertainty level

**PROJECT DEVELOPMENT HEURISTIC. NOT OFFICIAL HHGOA POLICY.**

```python
UncertaintyEngine(low_uncertainty_max=0.33, high_uncertainty_min=0.66)
# overall <= 0.33          -> LOW
# 0.33 < overall < 0.66    -> MEDIUM
# overall >= 0.66          -> HIGH
# overall is None          -> UNKNOWN
```

Both thresholds are constructor parameters, not hardcoded — a caller
that needs different bands (or the eventual real HHGoa policy, if one
exists) configures a different `UncertaintyEngine` instance rather than
editing this code.

## 10. Sufficiency for the next stage

**PROJECT DEVELOPMENT HEURISTIC. NOT OFFICIAL HHGOA POLICY.**

```python
sufficient_for_next_stage = (
    snapshot.status != FAILED
    and evidence_coverage >= min_coverage_for_sufficiency   # default 0.5, configurable
    and no HIGH-severity conflict was detected
)
```

This answers "is there enough *reliable* information for the next
reasoning/action layer to proceed?" — explicitly **not** "is there
enough evidence to declare fraud?" A `HIGH`-severity conflict
(`SIGNAL_DISAGREEMENT` or `DATA_INCONSISTENCY`) blocks sufficiency
because it means two facts that should agree do not — the underlying
evidence itself is not trustworthy yet, independent of how much of it
there is. A `FAILED` investigation is always insufficient (`False`, not
`None` — there is no ambiguity about this case).

## 11. Missing evidence — reasons stay distinct

| `MissingEvidenceReason` | Meaning | Example |
|---|---|---|
| `QUERY_ERROR` | The tool ran and failed | `find_shared_address_activity` hit a 500 |
| `NOT_INVESTIGATED` | The tool never ran | context failed first, or overall timeout skipped it |
| `DATA_MISSING` | The tool succeeded, but a specific required field was null | `transaction_context` succeeded but `product_cd` was `None` |
| `NOT_AVAILABLE` | Reserved for a source structurally incapable of returning data | unused by the current six tools |

`EMPTY` (the query ran fine and found nothing) is **never** listed in
`missing_evidence` — verified directly
(`TestMissingEvidenceDistinctness::test_empty_is_never_listed_as_missing_evidence`).
This is the single most important distinction in this phase: "no shared
device found" and "the device query failed" must never be conflated,
because a future uncertainty-aware reasoning layer needs to know which
one actually happened.

## 12. Rationale — deterministic, not LLM-generated

`_build_rationale` builds a plain string from the already-computed
factors (coverage %, quality average, completeness %, conflict
types/severities, missing-evidence reasons, final level) with template
formatting only — no free text generation, no LLM call, no randomness.
The same `InvestigationSnapshot` always produces byte-identical
rationale text
(`TestDeterminism::test_same_snapshot_produces_identical_assessment`,
also verified live).

## 13. Dataset label isolation — the critical anti-leakage guarantee

`UncertaintyEngine` never reads `EvidenceBundle.dataset_risk_score`
anywhere. Verified three ways:

1. **Behavioral**: the same evidence with `isFraud=True` vs.
   `isFraud=False` produces a byte-identical `UncertaintyAssessment`
   (`TestDatasetLabelIsolation`), both offline (mocked) and live (a real
   snapshot's `EvidenceBundle` copied with the label flipped).
2. **Static, AST-based**: `test_engine_source_never_reads_dataset_risk_score`
   parses `engine.py`'s own source and asserts no `.dataset_risk_score`
   attribute access or `"dataset_risk_score"` string-key access appears
   anywhere in the code (prose mentions in docstrings, which legitimately
   explain the omission, are not code and are excluded from this check).
3. **By construction**: `assess()`'s only snapshot fields ever read are
   `status`, `tool_results`, `evidence_bundle.evidence` (the `Evidence`
   list, never `evidence_bundle.dataset_risk_score` itself),
   `investigation_id`, and `transaction_id`.

## 14. Traceability

Every `UncertaintyAssessment.factors` entry names the factor, its value
(or `None`), and its `source` (e.g.
`"investigation_snapshot.tool_results"`,
`"evidence_bundle.evidence[*].quality"`) — so a later reasoning layer or
the eventual demo can show *why* a given uncertainty level was reached,
not just the final number.

## 15. Performance

Measured live: `UncertaintyEngine.assess()` averages **0.105ms** per
call over 20 runs against a real 6-tool `InvestigationSnapshot` — the
investigation that produced it took multiple seconds (Phase 2D). No
premature optimization was applied or needed; the engine performs zero
I/O and zero TigerGraph calls.

## 16. Fallback-dataset limitations (carried forward)

The current development fallback is **IEEE-CIS**, not official HHGoa
data. It has a binary `isFraud` label only — no continuous risk score.
This phase does not, and structurally cannot, invent a continuous risk
score from that binary label; `EvidenceBundle.dataset_risk_score`
(Phase 2B, unchanged) remains 1.0/0.0 and is never read by this
package at all (§13). Nothing in this uncertainty model should be read
as validated against, or representative of, real HHGoa bank policy.

## 17. Not built this phase (by design)

LangGraph, LLM reasoning, next-best-action, a policy engine, case
memory, GraphRAG, and the frontend are all explicitly out of scope.
`UncertaintyAssessment` is the intended input to Phase 2F (Policy /
Next-Best-Action Engine).
