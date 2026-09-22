/**
 * Phase 2L - fixtures shaped exactly like the backend's real OpenAPI
 * schema (backend/docs/phase-2-api.md, cross-checked against a live
 * /openapi.json fetch - see docs/phase-2-frontend.md). Values mirror an
 * actual observed live run against transaction 2987937, not invented
 * numbers.
 */

import type {
  CaseRecord,
  Evidence,
  HistoryResponse,
  InvestigationResponse,
  PolicyDecision,
  SimilarCasesResponse,
  UncertaintyAssessment,
} from "@/lib/types";

export const DEMO_TXN = "2987937";
export const DEMO_CASE_ID = "case-4e1e95885469438598800021bd03c2bd";

export const mockEvidence: Evidence[] = [
  {
    evidence_id: "2987937:shared_card:18227|583.0|150.0|226.0",
    evidence_type: "shared_card",
    transaction_id: DEMO_TXN,
    status: "SUCCESS",
    observation: "12 other transaction(s) share the same card identifier ('18227|583.0|150.0|226.0').",
    interpretation: "MEDIUM-quality signal: shared card fingerprint, moderate specificity.",
    quality: "MEDIUM",
    quality_reason: "shared card fingerprint, moderate specificity",
    related_entities: ["2987001", "2987010", "2987022"],
    metrics: { related_transaction_count: 12, related_fraud_count: 0 },
    error: null,
    provenance: {
      source: "tigergraph",
      source_query: "find_shared_card_activity",
      transaction_id: DEMO_TXN,
      entity_id: "18227|583.0|150.0|226.0",
      query_params: {},
    },
  },
  {
    evidence_id: "2987937:shared_address:299.0|87.0",
    evidence_type: "shared_address",
    transaction_id: DEMO_TXN,
    status: "SUCCESS",
    observation: "312 other transaction(s) share the same address identifier ('299.0|87.0').",
    interpretation: "LOW-quality signal: coarse regional/address signal.",
    quality: "LOW",
    quality_reason: "coarse regional/address signal",
    related_entities: ["2987002", "2987003"],
    metrics: { related_transaction_count: 312, related_fraud_count: 4 },
    error: null,
    provenance: {
      source: "tigergraph",
      source_query: "find_shared_address_activity",
      transaction_id: DEMO_TXN,
      entity_id: "299.0|87.0",
      query_params: {},
    },
  },
  {
    evidence_id: "2987937:shared_device:-",
    evidence_type: "shared_device",
    transaction_id: DEMO_TXN,
    status: "EMPTY",
    observation: "Transaction 2987937 has no linked Device.",
    interpretation: null,
    quality: "UNKNOWN",
    quality_reason: null,
    related_entities: [],
    metrics: {},
    error: null,
    provenance: {
      source: "tigergraph",
      source_query: "find_shared_device_activity",
      transaction_id: DEMO_TXN,
      entity_id: null,
      query_params: {},
    },
  },
];

export const mockUncertainty: UncertaintyAssessment = {
  investigation_id: "inv-fixed",
  transaction_id: DEMO_TXN,
  evidence_coverage: 0.83,
  signal_quality: 0.42,
  signal_conflict: 0.0,
  data_completeness: 0.9,
  overall_uncertainty: 0.178,
  uncertainty_level: "LOW",
  missing_evidence: [],
  conflicting_evidence: [],
  factors: [
    { factor: "evidence_coverage", value: 0.83, source: "uncertainty_engine", detail: "5/6 planned tools succeeded" },
  ],
  sufficient_for_next_stage: true,
  rationale: "Evidence coverage and signal quality are sufficient to proceed to policy evaluation.",
};

export const mockPolicy: PolicyDecision = {
  investigation_id: "inv-fixed",
  transaction_id: DEMO_TXN,
  action: "CREATE_CASE",
  approval_required: true,
  approval_route: "ANALYST",
  executable: false,
  requires_more_evidence: false,
  status: "RECOMMENDATION_READY",
  uncertainty_level: "LOW",
  overall_uncertainty: 0.178,
  evidence_ids: [mockEvidence[0].evidence_id],
  policy_basis: "PROJECT DEVELOPMENT HEURISTIC - NOT OFFICIAL HHGOA POLICY",
  rationale: "Low uncertainty with material shared-entity evidence warrants case creation for analyst review.",
};

export const mockInvestigationResponse: InvestigationResponse = {
  investigation_id: DEMO_CASE_ID,
  case_id: DEMO_CASE_ID,
  transaction_id: DEMO_TXN,
  status: "COMPLETED",
  completed: true,
  iterations: 1,
  tool_calls: 1,
  findings: [
    "12 other transaction(s) share the same card identifier ('18227|583.0|150.0|226.0').",
    "312 other transaction(s) share the same address identifier ('299.0|87.0').",
  ],
  evidence: mockEvidence,
  evidence_summary: { evidence_counts: { shared_card: 1, shared_address: 1 }, query_status: {} },
  uncertainty: mockUncertainty,
  policy_decision: mockPolicy,
  requested_evidence: [],
  historical_context: [],
  context: {
    context_format_version: "v1",
    transaction_id: DEMO_TXN,
    investigation_id: "inv-fixed",
    case_id: DEMO_CASE_ID,
    generated_at: "2026-09-22T14:00:00Z",
    current_evidence: [],
    historical_cases: [],
    recurring_patterns: [
      {
        context_id: "ctx-pattern-1",
        context_type: "DERIVED_PATTERN",
        content:
          "Recurring evidence pattern across 3 case(s): shared_address, shared_card. Related cases: case-a, case-b. Historical actions observed: CREATE_CASE. Historical outcomes observed: UNRESOLVED.",
        source: "case_memory_pattern_detection",
        provenance: "CaseMemory.detect_recurring_patterns: pattern_id=p1, occurrences=3",
        evidence_ids: [],
        case_ids: ["case-a", "case-b"],
        quality: null,
        relevance: 3,
      },
    ],
    missing_information: [],
    context_items: [],
    uncertainty_assessment: mockUncertainty,
    policy_decision: mockPolicy,
    truncated: false,
    truncation_notes: [],
  },
  explanation:
    "Transaction 2987937 shares a card fingerprint with 12 other transactions and an address with 312 others. Evidence coverage is high and no conflicts were detected, so uncertainty is LOW. A case was created for analyst review; no action has been executed.",
  next_step: "Awaiting ANALYST approval for the recommended action (CREATE_CASE).",
  error: null,
};

export const mockCaseRecord: CaseRecord = {
  case_id: DEMO_CASE_ID,
  status: "PENDING_REVIEW",
  trigger: "FRAUD_SIGNAL",
  transaction_id: DEMO_TXN,
  investigation_id: "inv-fixed",
  created_at: "2026-09-22T14:00:00Z",
  updated_at: "2026-09-22T14:00:01Z",
  evidence_ids: [mockEvidence[0].evidence_id, mockEvidence[1].evidence_id],
  evidence_types: ["shared_card", "shared_address"],
  findings: [
    {
      finding_id: "finding-1",
      description: "12 other transaction(s) share the same card identifier.",
      evidence_ids: [mockEvidence[0].evidence_id],
      quality: "MEDIUM",
      created_at: "2026-09-22T14:00:00Z",
    },
  ],
  decisions: [
    {
      decision_id: "decision-1",
      decision_type: "SYSTEM_RECOMMENDATION",
      rationale: mockPolicy.rationale,
      evidence_ids: [],
      actor: "policy_engine",
      approval_route: "ANALYST",
      approved: null,
      created_at: "2026-09-22T14:00:01Z",
    },
  ],
  actions: [
    {
      action_id: "action-1",
      action: "CREATE_CASE",
      status: "RECOMMENDED",
      rationale: mockPolicy.rationale,
      approval_required: true,
      approval_route: "ANALYST",
      executed: false,
      actor: "policy_engine",
      created_at: "2026-09-22T14:00:01Z",
    },
  ],
  outcome: null,
  uncertainty_assessment: mockUncertainty,
  policy_decision: mockPolicy,
  notes: [],
};

export const mockHistoryResponse: HistoryResponse = {
  case_id: DEMO_CASE_ID,
  events: [
    {
      event_type: "CASE_CREATED",
      occurred_at: "2026-09-22T14:00:00Z",
      description: `Case created for transaction ${DEMO_TXN} (trigger: FRAUD_SIGNAL).`,
      ref_id: DEMO_CASE_ID,
    },
    {
      event_type: "FINDING_ADDED",
      occurred_at: "2026-09-22T14:00:00.5Z",
      description: "12 other transaction(s) share the same card identifier.",
      ref_id: "finding-1",
    },
    {
      event_type: "RECOMMENDATION_CREATED",
      occurred_at: "2026-09-22T14:00:01Z",
      description: mockPolicy.rationale,
      ref_id: "decision-1",
    },
  ],
  note: "Derived only from timestamped CaseRecord fields (findings/decisions/actions/outcome).",
};

export const mockSimilarCasesResponse: SimilarCasesResponse = {
  case_id: DEMO_CASE_ID,
  similar_cases: [
    {
      case_id: "case-synthetic-1",
      similarity_score: 0.62,
      matching_features: ["shared_card", "shared_address"],
      relevant_findings: [],
      previous_decisions: [],
      previous_actions: [
        {
          action_id: "action-old-1",
          action: "CREATE_CASE",
          status: "APPROVED",
          rationale: "Prior synthetic case rationale.",
          approval_required: true,
          approval_route: "ANALYST",
          executed: false,
          actor: "policy_engine",
          created_at: "2026-09-01T00:00:00Z",
        },
      ],
      outcome: { outcome_type: "UNRESOLVED", notes: "", recorded_at: "2026-09-02T00:00:00Z", is_synthetic: true },
    },
  ],
  note: "Similarity is a PROJECT DEVELOPMENT HEURISTIC, not an official HHGoa system.",
};
