# Phase 2G — Case Management + Case Memory

**Historical cases used anywhere in this phase's tests are SYNTHETIC
DEVELOPMENT CASES** — the official HHGoa case dataset/policy artifacts
are not available in the current IEEE-CIS development fallback. Never
presented as real bank investigations. **Case-memory similarity is a
PROJECT DEVELOPMENT HEURISTIC**, not an official HHGoa retrieval system.

Status: **COMPLETE**. Offline: 39/39 PASS. Live: 7/7 PASS.

## 1. Purpose

Turns a completed investigation into a persistent, structured fraud
case, and provides deterministic retrieval of relevant historical cases.

```
InvestigationSnapshot + UncertaintyAssessment + PolicyDecision
        v
   CaseManager
        v
   CaseRecord
   /        \
CaseStore  CaseMemory
                v
        Historical Cases
```

The future agent sits above this deterministic backend — no LangGraph,
no LLM, no vector embeddings, no frontend, no API layer this phase.

## 2. Package layout

```
app/case/
    models.py    CaseStatus, CaseTrigger, CaseFinding, CaseDecision, CaseAction,
                 CaseOutcome, CaseRecord, SimilarCaseResult, RecurringPattern
    store.py     CaseStore (ABC), InMemoryCaseStore
    manager.py   CaseManager, the enforced state machine
    memory.py    CaseMemory, similarity formula, recurring-pattern detection
```

A pre-existing, unrelated Phase 0 stub already occupies `app/policies/`
(empty, untouched); this phase's package is the singular `app/case/`.

## 3. Domain model — reused, not duplicated

No new evidence/investigation/uncertainty/policy model was created.
`CaseFinding.quality` reuses Phase 2B's `SignalQuality`. `CaseAction`
reuses Phase 2F's `PolicyAction`/`ApprovalRoute`. `CaseRecord` embeds the
already-compact `UncertaintyAssessment` (Phase 2E) and `PolicyDecision`
(Phase 2F) in full, but references the (potentially large)
`InvestigationSnapshot`/`EvidenceBundle` only by `investigation_id` and
`evidence_ids` — "do not duplicate huge raw evidence payloads
unnecessarily. Prefer references where possible" (§4).

Every model is a frozen Pydantic model, matching every prior phase's
immutability convention (`Evidence`, `InvestigationSnapshot`,
`UncertaintyAssessment`, `PolicyDecision`). `CaseManager` never mutates a
`CaseRecord` in place — every update method reads the current record,
builds a new one via `model_copy(update=...)`, saves it, and returns it.

### CaseStatus (state machine)

```
OPEN -> INVESTIGATING
INVESTIGATING -> PENDING_EVIDENCE | ACTION_RECOMMENDED
PENDING_EVIDENCE -> INVESTIGATING
ACTION_RECOMMENDED -> PENDING_REVIEW
PENDING_REVIEW -> CLOSED | INVESTIGATING   (reviewer sends back for more work)
CLOSED -> (terminal - no outgoing transition)
```

`CaseManager.transition_status()` checks `LEGAL_TRANSITIONS` and raises
`InvalidCaseTransitionError` deterministically for anything outside it —
verified for both a valid chain and several invalid attempts, including
from the terminal `CLOSED` state.

### CaseTrigger

`FRAUD_SIGNAL` / `CUSTOMER_REPORT` / `ANALYST_REQUEST` / `AGENT_REQUEST`
/ `UNKNOWN`. `UNKNOWN` is the default — this project has no
customer-report intake or agent-request system built yet, so those only
appear when a caller explicitly asserts them; nothing is fabricated.

### CaseFinding / CaseDecision / CaseAction / CaseOutcome

- `CaseDecision.decision_type` is `SYSTEM_RECOMMENDATION` /
  `HUMAN_DECISION` / `APPROVAL` — kept explicitly distinguishable.
  `CaseManager.add_system_recommendation()` is the only path a
  `PolicyDecision` enters a case's decision log through, and it always
  sets `actor="policy_engine"` and `decision_type=SYSTEM_RECOMMENDATION`
  — never `HUMAN_DECISION`.
- `CaseAction.executed` starts `False` and stays `False` until
  `CaseManager.record_action_executed()` is called — which itself only
  *records* that a caller asserts an action happened via some external
  system; `CaseManager` performs no real-world action itself (there is
  no backend to perform one against — Phase 2F). Execution additionally
  requires the action to already be `APPROVED` via
  `record_action_review()`; an unapproved or rejected action can never
  be recorded executed (`InvalidCaseTransitionError`).
- `CaseOutcomeType` values (`CONFIRMED_FRAUD` / `CLEARED` / `UNRESOLVED`
  / `FALSE_POSITIVE` / `UNKNOWN`) are **not official HHGoa categories** —
  `CaseOutcome.is_synthetic` flags any dev/test outcome explicitly.

## 4. CaseManager API

```python
create_case(snapshot, *, uncertainty_assessment=None, policy_decision=None, trigger=UNKNOWN) -> CaseRecord
get_case(case_id) -> CaseRecord
transition_status(case_id, new_status) -> CaseRecord
add_finding(case_id, *, description, evidence_ids, quality=None) -> CaseRecord
add_decision(case_id, *, decision_type, rationale, evidence_ids, actor, ...) -> CaseRecord
add_system_recommendation(case_id, policy_decision) -> CaseRecord
add_action_from_policy_decision(case_id, policy_decision) -> CaseRecord
record_action_review(case_id, action_id, *, approved, actor) -> CaseRecord
record_action_executed(case_id, action_id, *, actor) -> CaseRecord
set_outcome(case_id, outcome_type, *, notes="", is_synthetic=False) -> CaseRecord
attach_evidence(case_id, evidence_ids) -> CaseRecord
```

The brief's required `update_case(...)` capability is realized as these
specific, typed methods rather than one generic
`update_case(case_id, **kwargs)` mutator — a deliberate choice for type
safety and validated transitions ("do not expose raw mutable
dictionaries as the primary API," §6). `id_generator` and `clock` are
constructor-injectable, exactly like `InvestigationService` (Phase 2D) —
tests never depend on `time.sleep()`, the real wall clock, or random IDs.

Case IDs are `case-<uuid4 hex>` by default — never the bare
`transaction_id` (a transaction could theoretically have more than one
investigation/case over time).

## 5. CaseStore — the persistence boundary

```python
class CaseStore(ABC):
    def save(self, case: CaseRecord) -> None: ...
    def get(self, case_id: str) -> CaseRecord | None: ...
    def list_all(self) -> list[CaseRecord]: ...
    def list_by_transaction(self, transaction_id: str) -> list[CaseRecord]: ...
```

Only `InMemoryCaseStore` ships this phase — not persistent across
process restarts, an explicit, documented limitation. Per the brief
(§7/§8), a large new TigerGraph schema was deliberately **not** built
this phase; the abstract `CaseStore` interface is the seam a future
`TigerGraphCaseStore(CaseStore)` (or any other persistent backend) could
implement without `CaseManager`/`CaseMemory` logic changing at all —
both depend only on the abstract interface, never on `InMemoryCaseStore`
specifically. `CaseMemory` deliberately reuses the same `CaseStore`
abstraction `CaseManager` writes to, rather than a second, duplicate
storage layer — a `CaseManager` and the `CaseMemory` that should see its
cases normally share one `CaseStore` instance.

## 6. CaseMemory and the similarity formula

```
similarity = (
    0.4 * jaccard(evidence_types_a, evidence_types_b)
  + 0.2 * (1.0 if uncertainty_level_a == uncertainty_level_b else 0.0)
  + 0.2 * (1.0 if policy_action_a == policy_action_b else 0.0)
  + 0.1 * jaccard(conflict_types_a, conflict_types_b)
  + 0.1 * (1.0 if trigger_a == trigger_b else 0.0)
)
```

`evidence_types` on `CaseRecord` is not "every evidence type the
investigation attempted" (Phase 2D's fixed six-tool plan means a
COMPLETED case would almost always have all six, making that useless for
discrimination) — it is the *material* evidence types (reusing Phase
2F's `material_evidence()` — SUCCESS status, at least one related
transaction), computed once at `create_case()` time. Two cases with *no*
evidence types in common score `0.0` on that component — "nothing in
common" is never treated as a match.

`SimilarityWeights` is constructor-injectable and validated to sum to
`1.0`. Each `SimilarCaseResult` carries `similarity_score`,
`matching_features` (human-readable strings explaining each weighted
component's contribution — explainability, per §10), and the retrieved
case's `relevant_findings` / `previous_decisions` / `previous_actions` /
`outcome`. `CaseMemory` never concludes anything from an outcome — it
only exposes it; nothing in this package states or implies "therefore
the current transaction is fraud" (§13).

Retrieval ranking is deterministic: sorted by `similarity_score`
descending, ties broken by `case_id` ascending — never dict/set
iteration order. Verified directly with two cases scoring identically.

### API

```python
store(case) -> None
retrieve_by_transaction(transaction_id) -> list[CaseRecord]
retrieve_by_evidence_pattern(evidence_types) -> list[CaseRecord]
retrieve_by_action(action) -> list[CaseRecord]
retrieve_by_outcome(outcome_type) -> list[CaseRecord]
retrieve_similar(case, *, limit=5, min_similarity=0.0) -> list[SimilarCaseResult]
detect_recurring_patterns(*, min_occurrences=2) -> list[RecurringPattern]
```

## 7. Recurring pattern detection

Groups all stored cases by their exact `evidence_types` set; a group
with `>= min_occurrences` (default 2) becomes a `RecurringPattern`
(`pattern_id`, `evidence_types`, `occurrences`, `related_case_ids`,
`historical_actions`, `historical_outcomes`). Deterministic ordering:
most-frequent first, `pattern_id` ascending tie-break. Always called a
**"recurring evidence pattern"** or **"recurring investigation
pattern"** — never a "fraud pattern," since occurrences are not
confirmed-fraud by construction (this phase's synthetic outcomes include
`CLEARED` and `CONFIRMED_FRAUD` side by side within the same pattern in
tests, precisely to prove the terminology is honest).

## 8. Historical decision/outcome usage — informational, never conclusive

A `SimilarCaseResult` exposes a prior case's action and outcome as
context (e.g. "similarity=0.81, previous action=CREATE_CASE, previous
outcome=CLEARED"). `CaseMemory` itself draws no conclusion from this — a
future agent decides what, if anything, to do with it. No code path in
`app/case/` outputs a verdict, a probability, or a recommendation based
on historical outcomes.

## 9. Label isolation — the strongest possible guarantee

`CaseRecord` **has no field** for the dataset's binary `isFraud` label
or `dataset_risk_score` at all — verified directly
(`test_case_record_has_no_dataset_label_field_at_all` asserts those
names are absent from `CaseRecord.model_fields`). This is stronger than
Phase 2E/2F's "the engine never reads it" guarantee: here, the value is
never even captured on the model a similarity computation could read.
Additionally verified:

- **Behavioral**: two investigations identical except `isFraud=True` vs.
  `False` produce byte-identical `CaseRecord`s (net of `case_id`/
  timestamps, held equal via injected id/clock).
- **Static, AST-based**: no `.dataset_risk_score`/`.isFraud`/`.is_fraud`
  attribute or matching string-key access anywhere in
  `models.py`/`manager.py`/`memory.py`/`store.py`.
- **Retrieval ordering**: proven identical across repeated runs on an
  identical store.

## 10. TigerGraph / current-graph-facts boundary

`app/case/` never imports `app.tigergraph.*` (AST-verified across all
four modules) and never calls TigerGraph. `CaseManager` and `CaseMemory`
operate entirely on already-built `InvestigationSnapshot`/
`UncertaintyAssessment`/`PolicyDecision`/`CaseRecord` objects a caller
hands them — TigerGraph holds *current* transaction/network facts,
`CaseMemory` holds *historical* investigation knowledge, and the two are
never conflated. This separation is deliberate groundwork for the later
GraphRAG/agent architecture, not built this phase.

## 11. Security

Secret scan: PASS. No TigerGraph secret/API key, Kaggle credential, or
OpenAI credential in any new file; `.env` remains untracked. No
destructive graph operations, arbitrary GSQL execution, dynamic code
execution, or uncontrolled external action execution — `CaseManager`/
`CaseMemory` perform zero I/O beyond the in-process `InMemoryCaseStore`.

## 12. Test results

Offline (`tests/unit/test_case_management.py`): **39/39 PASS** — case
creation, JSON round-trip, valid/invalid/terminal state transitions,
findings, decision-type distinctness, the full
recommend→approve→execute action lifecycle (and its guard against
executing an unapproved/rejected action), outcome recording, similarity
scoring and stable tie-breaking, evidence-pattern/action/outcome/
transaction retrieval, label isolation (behavioral + structural + static
+ retrieval-ordering), recurring-pattern detection (including a
below-threshold negative case), empty-memory handling, missing-metadata
robustness, the TigerGraph/LLM architectural boundary, and determinism.

Live (`tests/tigergraph/test_case_live.py`): **7/7 PASS** — a real case
built end-to-end from `InvestigationService → UncertaintyEngine →
PolicyEngine → CaseManager`, full traceability back to real evidence
IDs, a complete lifecycle on the real `PolicyDecision`, JSON
serialization, `CaseMemory` retrieval against a real case, and
negligible performance across all four operations measured.

## 13. Live example

Transaction `2987937`:

```
case_id: case-49d11bf1544a4b8e844716e2060c21e1
case status: OPEN
evidence_ids: [2987937:network_pattern:-, 2987937:shared_address:299.0|87.0,
               2987937:shared_card:18227|583.0|150.0|226.0, 2987937:shared_device:-,
               2987937:shared_email_domain:sbcglobal.net, 2987937:transaction_context:-]
uncertainty_level: LOW
policy action: CREATE_CASE
approval_required: True
approval_route: ANALYST
executable: False
```

No value here is hardcoded into `CaseManager` — it consumes whatever
`PolicyDecision` it is actually handed.

## 14. Performance (live measurements)

| Operation | Avg latency |
|---|---|
| Case creation | 0.044 ms |
| Case update (add_finding) | 0.022 ms |
| Case serialization | 0.029 ms |
| CaseMemory retrieval | 0.347 ms |

All negligible next to the multi-second TigerGraph investigation, and
consistent with Phase 2E's (~0.105ms) and Phase 2F's (~0.024ms) own
pure-computation measurements. No TigerGraph calls originate from
`app/case/` at any point.

## 15. Known limitations

- No persistent `CaseStore` implementation ships this phase (by design —
  the brief explicitly asked to establish the domain model and boundary
  first, not a TigerGraph schema).
- `CaseTrigger.CUSTOMER_REPORT`/`ANALYST_REQUEST`/`AGENT_REQUEST` are
  declared but this project has no real intake system for any of them
  yet — they only appear when a caller explicitly asserts one.
- `CaseOutcomeType` categories are this project's own development
  vocabulary, not official HHGoa outcome categories, which do not exist
  in the current fallback dataset.
- The similarity formula's weights (0.4/0.2/0.2/0.1/0.1) are a
  documented, configurable, transparent starting point — not validated
  against real analyst similarity judgments, which this project has no
  source for.
- Inherited, unchanged this phase: the pyTigerGraph 2.0.4 TLS-
  verification issue (Phase 2B) and the IEEE-CIS binary-label-only
  limitation (irrelevant to this package specifically, since it cannot
  even read that label — §9).

## 16. Future GraphRAG integration

`CaseRecord`/`CaseMemory` are designed as the retrieval substrate a
future GraphRAG or agent layer would query — structured, deterministic,
and already separated from live TigerGraph facts (§10). Nothing in this
phase precludes later adding embeddings-based retrieval *alongside* this
deterministic layer (e.g. as an additional `CaseMemory` implementation
or a hybrid ranker) without changing `CaseManager` or the `CaseStore`
boundary — but that is explicitly Phase 2H+ work, not built here.

## 17. Final architecture confirmed

```
TigerGraph -> Investigation Queries -> Evidence Model -> Tool Registry
  -> InvestigationService -> InvestigationSnapshot -> UncertaintyEngine
  -> UncertaintyAssessment -> Policy/NBA Engine -> PolicyDecision
  -> CaseManager -> CaseStore / CaseMemory -> Historical Cases
```

The LLM/agent layer is not part of Phase 2G.
