# Phase 2F — Policy / Next-Best-Action Engine

**POLICY STATUS: PROJECT DEVELOPMENT HEURISTIC — NOT OFFICIAL HHGOA
POLICY.** No action limit, approval route, or threshold in this package
is an official HHGoa rule. The official HHGoa policy artifacts are not
available in the current development fallback dataset (IEEE-CIS).
`PolicyEngine` does not execute real-world actions; it produces
deterministic recommendations and approval metadata only.

Status: **COMPLETE**. Offline: 30/30 PASS. Live: 9/9 PASS.

## 1. Purpose

```
InvestigationSnapshot (Phase 2D) + UncertaintyAssessment (Phase 2E)
        |
        v
PolicyEngine.evaluate()     <- pure computation, no TigerGraph, no LLM
        |
        v
PolicyDecision
```

Converts investigation evidence + uncertainty into a recommended next
action and an approval route, while keeping three concepts strictly
separate: **recommendation** (`action`), **authorization**
(`approval_required` / `approval_route`), and **executability**
(`executable`, always `False` this phase). This is not the final agent —
no LangGraph, no LLM, no case memory, no frontend.

## 2. Architecture

```
app/policy/
    models.py    PolicyAction, ApprovalRoute, PolicyStatus, PolicyDecision
    engine.py    PolicyEngine
```

`PolicyEngine.evaluate(snapshot, assessment)` takes only the Phase 2D
`InvestigationSnapshot` and Phase 2E `UncertaintyAssessment` — nothing
else. It never imports `app.tigergraph` (verified by AST-parsing this
package's own imports, same technique as Phase 2E) and never calls
TigerGraph, pyTigerGraph, GSQL, MCP, REST++, or any external API.

An empty, pre-existing `app/policies/` stub (Phase 0 scaffold, unrelated
to this phase, left untouched) already existed in the repo; this phase's
new package is the singular `app/policy/`, matching the brief's naming.

## 3. Input models — reused, not duplicated

No new evidence, snapshot, or uncertainty model was created. `PolicyEngine`
reads directly from:

- `InvestigationSnapshot.status` (Phase 2D) — `FAILED` gates everything else
- `InvestigationSnapshot.evidence_bundle.evidence` (Phase 2B `Evidence`,
  unchanged) — for material-evidence and quality checks
- `UncertaintyAssessment.uncertainty_level`, `.overall_uncertainty`,
  `.sufficient_for_next_stage`, `.conflicting_evidence` (Phase 2E,
  unchanged)

## 4. Output model: `PolicyDecision`

```python
class PolicyDecision(BaseModel):
    investigation_id: str
    transaction_id: str
    action: PolicyAction
    approval_required: bool
    approval_route: ApprovalRoute
    executable: bool                      # always False this phase
    requires_more_evidence: bool
    status: PolicyStatus
    uncertainty_level: UncertaintyLevel   # reused from Phase 2E
    overall_uncertainty: float | None
    evidence_ids: list[str]
    policy_basis: str                     # always starts with the disclaimer
    rationale: str
```

Frozen Pydantic model, fully `model_dump(mode="json")`-serializable, and
round-trips through JSON unchanged (`TestSerialization`). No
`fraud_probability`, `fraud_verdict`, or `final_fraud_score` field exists
— enforced by `test_no_fraud_field_exists_on_the_decision_model`.

## 5. Supported actions

```python
class PolicyAction(str, Enum):
    ALLOW_TRANSACTION = "ALLOW_TRANSACTION"
    BLOCK_TRANSACTION = "BLOCK_TRANSACTION"
    MONITOR_ACCOUNT = "MONITOR_ACCOUNT"
    WARN_CUSTOMER = "WARN_CUSTOMER"
    CREATE_CASE = "CREATE_CASE"
    FILE_REPORT = "FILE_REPORT"              # declared, not reachable this phase
    REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"
    ESCALATE_ANALYST = "ESCALATE_ANALYST"
```

All eight match the challenge's action space. `FILE_REPORT` is declared
for vocabulary completeness but **not reachable by the current
heuristic** — filing a regulatory report needs a specific official
threshold this project does not fabricate (Phase 2F's explicit
constraint: no fabricated regulatory requirements). This follows the
same "declared but reserved" precedent as Phase 2C's `EMPTY_RESULT` and
Phase 2E's `MissingEvidenceReason.NOT_AVAILABLE`.

Because the fallback graph has no real `Customer`/`Account` vertex,
`WARN_CUSTOMER`/`MONITOR_ACCOUNT`/`BLOCK_TRANSACTION` are recommendation
contracts only — `executable` is `False` on every decision this engine
produces (§9).

## 6. Approval model

```python
class ApprovalRoute(str, Enum):
    NONE = "NONE"
    ANALYST = "ANALYST"
    SENIOR_ANALYST = "SENIOR_ANALYST"
    COMPLIANCE = "COMPLIANCE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
```

Project-defined development abstractions, not official HHGoa
authorization routing. `COMPLIANCE` and `MANUAL_REVIEW` are declared for
vocabulary completeness; the current heuristic reaches only `NONE`,
`ANALYST`, and `SENIOR_ANALYST` (§8's gate table).

`ALLOW_TRANSACTION` and `REQUEST_MORE_EVIDENCE` never require approval
(`approval_required=False`, `approval_route=NONE`) — neither has a
real-world consequence to authorize. Every other action sets
`approval_required=True` with a specific route.

## 7. Development heuristic — the exact decision logic

Three gates, evaluated in order:

**Gate 1 — cannot act at all.**
`snapshot.status == FAILED` or `uncertainty_level == UNKNOWN` →
`REQUEST_MORE_EVIDENCE`, `status=NOT_ACTIONABLE`, no approval needed —
there is nothing yet to approve or deny.

**Gate 2 — investigation ran, but Phase 2E judged it insufficient**
(`not assessment.sufficient_for_next_stage`):
- a **HIGH-severity** conflict is present (`SIGNAL_DISAGREEMENT` or
  `DATA_INCONSISTENCY`) → `ESCALATE_ANALYST`,
  `status=HUMAN_REVIEW_REQUIRED`, route `SENIOR_ANALYST`. Collecting
  more of the *same* evidence would not resolve a genuine factual
  disagreement.
- otherwise (insufficient due to low coverage) → `REQUEST_MORE_EVIDENCE`,
  `status=MORE_EVIDENCE_REQUIRED`, no approval needed.

**Gate 3 — sufficient for the next stage.** (Gate 2 guarantees no
HIGH-severity conflict remains here — Phase 2E's own sufficiency rule
already excludes that case.)

"Material evidence" = `SUCCESS`-status `shared_card`/`shared_device`/
`shared_address`/`shared_email_domain` items with at least one related
transaction. A query that ran and found nothing (`EMPTY`) is never
material — preserving Phase 2B/2E's EMPTY-is-not-a-signal distinction
exactly.

| Condition | Action | Approval route |
|---|---|---|
| No material evidence at all | `ALLOW_TRANSACTION` | none |
| `uncertainty_level == HIGH` (material evidence exists) | `ESCALATE_ANALYST` | ANALYST |
| `MEDIUM`, ≥1 MEDIUM/HIGH-quality material item, no conflict | `CREATE_CASE` | ANALYST |
| `MEDIUM`, otherwise (only LOW-quality and/or a conflict) | `MONITOR_ACCOUNT` | ANALYST |
| `LOW`, any conflict remains (only LOW/MEDIUM severity possible here) | `CREATE_CASE` | ANALYST |
| `LOW`, only LOW-quality material, ≥2 distinct types | `WARN_CUSTOMER` | ANALYST |
| `LOW`, only LOW-quality material, 1 type | `MONITOR_ACCOUNT` | ANALYST |
| `LOW`, ≥1 MEDIUM/HIGH-quality material item, ≥2 distinct types corroborate | `BLOCK_TRANSACTION` | SENIOR_ANALYST |
| `LOW`, ≥1 MEDIUM/HIGH-quality material item, single type only | `CREATE_CASE` | ANALYST |

`min_distinct_types_for_block` (default 2) is a constructor parameter,
not hardcoded — a different, project-specific threshold can be
configured without editing this code.

**A completed investigation with weak/no meaningful signal never
receives a punitive action**, regardless of the numeric uncertainty
level — the "no material evidence" row is checked first, before the
uncertainty-level branch (Phase 2F §6). **Uncertainty is never conflated
with fraud probability**: `overall_uncertainty` measures how
complete/reliable the investigation is, and this table never reads it as
"low uncertainty = fraud."

## 8. Uncertainty-aware behavior

- **High uncertainty** → `ESCALATE_ANALYST` (material evidence) or
  `REQUEST_MORE_EVIDENCE`/`ESCALATE_ANALYST` (Gate 1/2) — never an
  aggressive or irreversible recommendation.
- **Medium uncertainty** → `CREATE_CASE` or `MONITOR_ACCOUNT`, depending
  on evidence strength and conflicts — never `BLOCK_TRANSACTION`.
- **Low uncertainty** → the strongest reachable action
  (`BLOCK_TRANSACTION`) is possible, but only when evidence is both
  MEDIUM/HIGH-quality *and* corroborated across ≥2 distinct types with
  no remaining conflict — recommendation, still gated by
  `approval_required=True` and a senior-analyst route, never
  authorization.

## 9. Executability

`executable` is `False` on **every** `PolicyDecision` this phase
produces — verified across all eight `PolicyAction` values
(`TestExecutability::test_every_action_is_marked_non_executable`, and
against a real live decision). This project has no real backend (payment
processor, customer messaging system, case management system, regulator
filing API) capable of actually carrying out any of these actions yet.
Every `rationale` ends with an explicit "This is a recommendation only -
no real-world action has been executed."

## 10. Traceability

Every `PolicyDecision.evidence_ids` is the exact, sorted list of
`Evidence.evidence_id` values from `snapshot.evidence_bundle.evidence` —
verified live that nothing is dropped and nothing invented
(`test_evidence_ids_trace_back_to_the_real_evidence_bundle`: the set
matches exactly). `policy_basis` names the specific gate that fired
(e.g. "Gate 3f: LOW uncertainty, MEDIUM/HIGH-quality evidence
corroborated across 2 distinct type(s)"); `rationale` is a deterministic,
template-built sentence referencing the actual coverage/quality/conflict
facts that drove the decision — never vague text like "the transaction
appears suspicious."

## 11. Dataset-label isolation

`PolicyEngine` never reads `EvidenceBundle.dataset_risk_score` (or
`isFraud`/`is_fraud`/`is_fraud_label`) anywhere. Verified three ways,
mirroring Phase 2E's proof exactly:

1. **Behavioral, offline and live**: identical evidence with
   `isFraud=True` vs. `isFraud=False` (offline) or the real label
   flipped on a live snapshot produces a byte-identical `PolicyDecision`.
2. **Static, AST-based**: `test_engine_source_never_reads_dataset_label`
   parses `engine.py`'s own source and asserts no `.dataset_risk_score`/
   `.isFraud`/`.is_fraud`/`.is_fraud_label` attribute access or matching
   string-key access appears anywhere in the code.
3. **By construction**: `evaluate()`'s only inputs are `snapshot.status`,
   `snapshot.evidence_bundle.evidence` (never `.dataset_risk_score`),
   and `assessment`'s uncertainty fields — the dataset label is not on
   the call path at all.

## 12. Security

Secret scan: PASS — no TigerGraph secret/API key, Kaggle credential, or
OpenAI credential in any new file; `.env` remains untracked. No
destructive TigerGraph operations, arbitrary GSQL execution, dynamic
code execution (`eval`/`exec`), or unsafe external action execution
introduced — `PolicyEngine` performs zero I/O of any kind.

## 13. Test results

Offline (`tests/unit/test_policy_engine.py`): **30/30 PASS**, covering
determinism, dataset-label isolation (behavioral + static), high/low
uncertainty behavior, context-failure handling, insufficient-coverage
handling, HIGH-severity-conflict escalation, EMPTY-vs-ERROR evidence
distinctness, LOW/MEDIUM-severity conflict capping, LOW-quality address
evidence (including the "large count must not escalate" regression,
tied explicitly to Phase 2E's own `QUALITY_DISPARITY` threshold rather
than to the raw count), approval separation, non-executability across
every action, JSON round-trip, and the TigerGraph/LLM architectural
boundary.

Live (`tests/tigergraph/test_policy_live.py`): **9/9 PASS** — real
snapshot/assessment accepted, evidence IDs trace back exactly, no label
leakage on a real decision, no action falsely claimed executed, JSON
serializable, `policy_basis` declares the disclaimer, and engine runtime
measured.

## 14. Live example

Transaction `2987937` (the project's standing live fixture), sequential
investigation, real graph state at test time:

```
uncertainty_level: LOW
overall_uncertainty: 0.178
recommended action: CREATE_CASE
approval_required: True
approval_route: ANALYST
executable: False
status: RECOMMENDATION_READY
evidence_ids: [2987937:network_pattern:-, 2987937:shared_address:299.0|87.0,
               2987937:shared_card:18227|583.0|150.0|226.0, 2987937:shared_device:-,
               2987937:shared_email_domain:sbcglobal.net, 2987937:transaction_context:-]
rationale: "Uncertainty is LOW and material evidence was found, but at
least one evidence conflict remains - this caps the recommendation at
CREATE_CASE rather than escalating to BLOCK_TRANSACTION. Approval
required: True (route: ANALYST). This is a recommendation only - no
real-world action has been executed."
```

This is a clean demonstration of the heuristic working on real data:
`find_shared_card_activity` alone is strong enough evidence that a naive
count-based rule might reach for `BLOCK_TRANSACTION`, but Phase 2E's
`QUALITY_DISPARITY` conflict (address's 312-related-transaction fan-out
dominating the network under LOW quality) correctly caps the
recommendation at `CREATE_CASE` — evidence-aware, not count-driven.

## 15. Performance

`PolicyEngine.evaluate()` averages **0.024ms** per call (50 live runs) —
negligible next to the multi-second investigation, and Phase 2E's own
~0.105ms. No TigerGraph calls originate from this package at any point.

## 16. Known limitations

- `FILE_REPORT` and approval routes `COMPLIANCE`/`MANUAL_REVIEW` are
  declared for vocabulary completeness but not reachable by the current
  heuristic — a real regulatory-filing threshold is not available in
  this fallback dataset and this project does not fabricate one.
- The heuristic's thresholds (`min_distinct_types_for_block`, and the
  gate structure itself) are this project's own development choices,
  not validated against real HHGoa policy, which does not exist in the
  current fallback dataset. Any resemblance to a real bank's escalation
  ladder is coincidental convenience, not a claim of correctness.
- Inherited from Phase 2B (unchanged, not modified this phase): the
  IEEE-CIS fallback provides only a binary `isFraud` label, no continuous
  risk score — irrelevant to this engine specifically, since it never
  reads that label at all (§11).
- Inherited from Phase 2B (unchanged, not modified this phase): the
  pyTigerGraph 2.0.4 TLS-verification issue remains open, undocumented
  change, no suppression.
- Inherited environmental issue (Phase 2D/2E, not a Phase 2F defect):
  under sustained heavy live TigerGraph load across a long test session,
  isolated pre-existing live tests can intermittently fail and pass
  cleanly on immediate re-run in isolation — see the Phase 2F final
  report for this run's specific instance, if any.

## 17. Final architecture

```
TigerGraph -> Investigation Queries -> Evidence Model -> Tool Registry
  -> InvestigationService -> InvestigationSnapshot -> UncertaintyEngine
  -> UncertaintyAssessment -> Policy/NBA Engine -> PolicyDecision
```

The LLM/agent layer is not part of Phase 2F — this separation is
intentional. `PolicyDecision` is the intended input to whatever consumes
it next (case memory, LangGraph orchestration, or a UI), none of which
are built in this phase.
