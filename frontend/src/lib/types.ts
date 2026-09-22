/**
 * Phase 2L - TypeScript types mirroring the backend's actual OpenAPI
 * schema exactly (fetched live from a running `backend/` instance at
 * /openapi.json - see docs/phase-2-frontend.md "API integration").
 *
 * No field here was guessed. Anything the backend does not expose is
 * simply absent from these types - never invented to make a UI section
 * look more complete than the data actually is.
 */

// ---------------------------------------------------------------- enums

export type AgentStatus = "RUNNING" | "COMPLETED" | "LIMIT_REACHED" | "FAILED";

export type ApprovalRoute = "NONE" | "ANALYST" | "SENIOR_ANALYST" | "COMPLIANCE" | "MANUAL_REVIEW";

export type CaseActionStatus = "RECOMMENDED" | "APPROVED" | "REJECTED" | "EXECUTED";

export type CaseOutcomeType = "CONFIRMED_FRAUD" | "CLEARED" | "UNRESOLVED" | "FALSE_POSITIVE" | "UNKNOWN";

export type CaseStatus =
  | "OPEN"
  | "INVESTIGATING"
  | "PENDING_EVIDENCE"
  | "ACTION_RECOMMENDED"
  | "PENDING_REVIEW"
  | "CLOSED";

export type CaseTrigger = "FRAUD_SIGNAL" | "CUSTOMER_REPORT" | "ANALYST_REQUEST" | "AGENT_REQUEST" | "UNKNOWN";

export type ConflictSeverity = "LOW" | "MEDIUM" | "HIGH";

export type ConflictType = "SIGNAL_DISAGREEMENT" | "QUALITY_DISPARITY" | "MISSING_CONTEXT" | "DATA_INCONSISTENCY";

export type ContextItemType = "CURRENT_FACT" | "HISTORICAL_CASE" | "DERIVED_PATTERN" | "MISSING_INFORMATION";

export type DecisionType = "SYSTEM_RECOMMENDATION" | "HUMAN_DECISION" | "APPROVAL";

export type EvidenceType =
  | "transaction_context"
  | "shared_card"
  | "shared_device"
  | "shared_address"
  | "shared_email_domain"
  | "network_pattern";

export type HistoryEventType =
  | "CASE_CREATED"
  | "FINDING_ADDED"
  | "RECOMMENDATION_CREATED"
  | "APPROVAL_RECORDED"
  | "ACTION_RECORDED"
  | "OUTCOME_RECORDED";

export type MissingEvidenceReason = "QUERY_ERROR" | "NOT_INVESTIGATED" | "DATA_MISSING" | "NOT_AVAILABLE";

export type PolicyAction =
  | "ALLOW_TRANSACTION"
  | "BLOCK_TRANSACTION"
  | "MONITOR_ACCOUNT"
  | "WARN_CUSTOMER"
  | "CREATE_CASE"
  | "FILE_REPORT"
  | "REQUEST_MORE_EVIDENCE"
  | "ESCALATE_ANALYST";

export type PolicyStatus = "RECOMMENDATION_READY" | "MORE_EVIDENCE_REQUIRED" | "HUMAN_REVIEW_REQUIRED" | "NOT_ACTIONABLE";

export type QueryStatus = "SUCCESS" | "EMPTY" | "ERROR";

export type RequestedEvidenceStatus = "REQUESTED";

export type RequestedEvidenceType =
  | "CUSTOMER_VALIDATION"
  | "STEP_UP_AUTHENTICATION"
  | "APPROVED_PARTY_REQUEST"
  | "ANALYST_REVIEW";

export type SignalQuality = "LOW" | "MEDIUM" | "HIGH" | "UNKNOWN";

export type UncertaintyLevel = "LOW" | "MEDIUM" | "HIGH" | "UNKNOWN";

// ---------------------------------------------------------------- evidence

export interface QueryError {
  error_type: string;
  message: string;
}

export interface Provenance {
  source: string;
  source_query: string;
  transaction_id: string;
  entity_id: string | null;
  query_params: Record<string, unknown>;
}

export interface Evidence {
  evidence_id: string;
  evidence_type: EvidenceType;
  transaction_id: string;
  status: QueryStatus;
  observation: string | null;
  interpretation: string | null;
  quality: SignalQuality;
  quality_reason: string | null;
  related_entities: string[];
  metrics: Record<string, unknown>;
  error: QueryError | null;
  provenance: Provenance;
}

// ---------------------------------------------------------------- uncertainty

export interface MissingEvidence {
  evidence_type: string;
  reason: MissingEvidenceReason;
  source_status: QueryStatus | null;
  impact: string;
}

export interface EvidenceConflict {
  conflict_id: string;
  evidence_ids: string[];
  conflict_type: ConflictType;
  description: string;
  severity: ConflictSeverity;
}

export interface UncertaintyFactor {
  factor: string;
  value: number | null;
  source: string;
  detail: string;
}

export interface UncertaintyAssessment {
  investigation_id: string;
  transaction_id: string;
  evidence_coverage: number;
  signal_quality: number | null;
  signal_conflict: number;
  data_completeness: number | null;
  overall_uncertainty: number | null;
  uncertainty_level: UncertaintyLevel;
  missing_evidence: MissingEvidence[];
  conflicting_evidence: EvidenceConflict[];
  factors: UncertaintyFactor[];
  sufficient_for_next_stage: boolean;
  rationale: string;
}

// ---------------------------------------------------------------- policy

export interface PolicyDecision {
  investigation_id: string;
  transaction_id: string;
  action: PolicyAction;
  approval_required: boolean;
  approval_route: ApprovalRoute;
  executable: boolean;
  requires_more_evidence: boolean;
  status: PolicyStatus;
  uncertainty_level: UncertaintyLevel;
  overall_uncertainty: number | null;
  evidence_ids: string[];
  policy_basis: string;
  rationale: string;
}

// ---------------------------------------------------------------- requested evidence

export interface RequestedEvidence {
  request_id: string;
  requested_evidence_type: RequestedEvidenceType;
  reason: string;
  uncertainty_factor: string;
  approval_required: boolean;
  status: RequestedEvidenceStatus;
  requested_at: string;
}

// ---------------------------------------------------------------- context (GraphRAG)

export interface ContextItem {
  context_id: string;
  context_type: ContextItemType;
  content: string;
  source: string;
  provenance: string;
  evidence_ids: string[];
  case_ids: string[];
  quality: SignalQuality | null;
  relevance: number | null;
}

export interface InvestigationContext {
  context_format_version: string;
  transaction_id: string;
  investigation_id: string | null;
  case_id: string | null;
  generated_at: string;
  current_evidence: ContextItem[];
  historical_cases: ContextItem[];
  recurring_patterns: ContextItem[];
  missing_information: ContextItem[];
  context_items: ContextItem[];
  uncertainty_assessment: UncertaintyAssessment | null;
  policy_decision: PolicyDecision | null;
  truncated: boolean;
  truncation_notes: string[];
}

// ---------------------------------------------------------------- case

export interface CaseFinding {
  finding_id: string;
  description: string;
  evidence_ids: string[];
  quality: SignalQuality | null;
  created_at: string;
}

export interface CaseDecision {
  decision_id: string;
  decision_type: DecisionType;
  rationale: string;
  evidence_ids: string[];
  actor: string;
  approval_route: ApprovalRoute | null;
  approved: boolean | null;
  created_at: string;
}

export interface CaseAction {
  action_id: string;
  action: PolicyAction;
  status: CaseActionStatus;
  rationale: string;
  approval_required: boolean;
  approval_route: ApprovalRoute;
  executed: boolean;
  actor: string;
  created_at: string;
}

export interface CaseOutcome {
  outcome_type: CaseOutcomeType;
  notes: string;
  recorded_at: string;
  is_synthetic: boolean;
}

export interface CaseRecord {
  case_id: string;
  status: CaseStatus;
  trigger: CaseTrigger;
  transaction_id: string;
  investigation_id: string | null;
  created_at: string;
  updated_at: string;
  evidence_ids: string[];
  evidence_types: string[];
  findings: CaseFinding[];
  decisions: CaseDecision[];
  actions: CaseAction[];
  outcome: CaseOutcome | null;
  uncertainty_assessment: UncertaintyAssessment | null;
  policy_decision: PolicyDecision | null;
  notes: string[];
}

export interface SimilarCaseResult {
  case_id: string;
  similarity_score: number;
  matching_features: string[];
  relevant_findings: CaseFinding[];
  previous_decisions: CaseDecision[];
  previous_actions: CaseAction[];
  outcome: CaseOutcome | null;
}

// ---------------------------------------------------------------- API request/response envelopes

export interface InvestigationRequest {
  transaction_id: string;
  trigger?: CaseTrigger;
}

export interface InvestigationResponse {
  investigation_id: string;
  case_id: string | null;
  transaction_id: string;
  status: AgentStatus;
  completed: boolean;
  iterations: number;
  tool_calls: number;
  findings: string[];
  evidence: Evidence[];
  evidence_summary: Record<string, unknown>;
  uncertainty: UncertaintyAssessment | null;
  policy_decision: PolicyDecision | null;
  requested_evidence: RequestedEvidence[];
  historical_context: SimilarCaseResult[];
  context: InvestigationContext | null;
  explanation: string | null;
  next_step: string;
  error: string | null;
}

export interface EvidenceResponse {
  case_id: string;
  transaction_id: string;
  detail_available: boolean;
  evidence: Evidence[];
  evidence_ids: string[];
  evidence_types: string[];
  note: string | null;
}

export interface HistoryEvent {
  event_type: HistoryEventType;
  occurred_at: string;
  description: string;
  ref_id: string | null;
}

export interface HistoryResponse {
  case_id: string;
  events: HistoryEvent[];
  note: string;
}

export interface SimilarCasesResponse {
  case_id: string;
  similar_cases: SimilarCaseResult[];
  note: string;
}

export interface ContextResponse {
  case_id: string;
  available: boolean;
  context: InvestigationContext | null;
  note: string | null;
}

export interface DependencyStatus {
  name: string;
  checked: boolean;
  healthy: boolean | null;
  detail: string | null;
  latency_ms: number | null;
}

export interface HealthResponse {
  status: string;
  service: string;
}

export interface DetailedHealthResponse {
  status: string;
  service: string;
  dependencies: DependencyStatus[];
}

// ---------------------------------------------------------------- errors

export interface ApiErrorBody {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export interface ApiErrorEnvelope {
  error: ApiErrorBody;
}

/** Thrown by lib/api.ts for any non-2xx response - always carries the
 * backend's own structured error code/message, never a raw stack trace
 * (the backend itself never sends one - see backend/docs/phase-2-api.md
 * §6/§10). */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.details = body.details;
  }
}
