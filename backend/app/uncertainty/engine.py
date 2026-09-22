"""Phase 2E - the deterministic uncertainty engine.

    InvestigationSnapshot (Phase 2D)
            v
    UncertaintyEngine.assess()
            v
    UncertaintyAssessment

This module never imports `app.tigergraph` and never calls TigerGraph -
it operates entirely on the `InvestigationSnapshot` it is given.
Verified by tests/unit/test_uncertainty_engine.py::TestArchitecturalBoundary,
which parses this file's own import statements with `ast` rather than
trusting a docstring claim.

This module never reads `EvidenceBundle.dataset_risk_score` (the
dataset's binary isFraud label, Phase 2B) anywhere in its computation -
see TestDatasetLabelIsolation in the test module, which runs the same
snapshot with the label flipped and asserts an identical
UncertaintyAssessment. Leaking the answer into the evidence assessment
is exactly what this phase's brief prohibits.

No LLM, no randomness, no wall-clock dependency: `assess()` is a pure
function of its `InvestigationSnapshot` argument (plus the engine's own
fixed, documented configuration) - the same snapshot always produces
byte-identical output.

## Formulas (see docs/phase-2-uncertainty-model.md for the full writeup)

- `evidence_coverage` = (planned sources that completed SUCCESS or EMPTY) / 6.
  ERROR and never-run both count as *not* covered - Phase 2E §7/§8.

- `signal_quality` = average of the internal LOW=0.33/MEDIUM=0.66/HIGH=1.0
  normalization, over evidence items that are SUCCESS *and* carry one of
  those three ratings. UNKNOWN-quality items (transaction_context always,
  and any EMPTY/ERROR item) are excluded, not scored as 0 - otherwise the
  ever-present UNKNOWN transaction_context item would silently drag every
  assessment down regardless of the actual evidence obtained. `None` when
  no such item exists.

- `data_completeness` = fraction of transaction_context's three
  documented-nullable dataset fields (is_fraud_label, transaction_amt,
  product_cd) that are populated - the only place in the snapshot this
  engine has real, individual-field nullability visibility. `None` when
  transaction_context itself did not succeed.

- `signal_conflict` = min(1.0, sum(severity weight of each detected
  conflict) / 4.0). Each of the four conflict rules below fires at most
  once per assessment (aggregating every affected evidence item into one
  conflict record), so 4.0 is the true maximum possible weight, not an
  arbitrary cap - severity weights are LOW=0.25/MEDIUM=0.5/HIGH=1.0.

- `overall_uncertainty` = average of {1-coverage, signal_conflict,
  1-quality, 1-completeness}, using only the factors that are actually
  available (never substituting 0 for an unavailable one) - Phase 2E
  §21. `None` (UNKNOWN) only when the investigation itself is FAILED
  (transaction context could not be established at all), since in that
  case `signal_conflict` would be a vacuous 0.0 (nothing to check) rather
  than a meaningful assessment.
"""

from __future__ import annotations

from app.evidence.models import Evidence, EvidenceType, QueryStatus, SignalQuality
from app.investigation.snapshot import (
    INVESTIGATION_PLAN,
    InvestigationSnapshot,
    InvestigationStatus,
)
from app.uncertainty.models import (
    ConflictSeverity,
    ConflictType,
    EvidenceConflict,
    MissingEvidence,
    MissingEvidenceReason,
    UncertaintyAssessment,
    UncertaintyFactor,
    UncertaintyLevel,
)

_TOTAL_PLANNED_SOURCES = len(INVESTIGATION_PLAN)  # 6

# Internal evidence-quality normalization ONLY - not a bank score, not a
# fraud probability, not official HHGoa policy (Phase 2E §11).
_QUALITY_SCORE: dict[SignalQuality, float] = {
    SignalQuality.LOW: 0.33,
    SignalQuality.MEDIUM: 0.66,
    SignalQuality.HIGH: 1.0,
}

_CONTEXT_REQUIRED_FIELDS = ("is_fraud_label", "transaction_amt", "product_cd")

_SEVERITY_WEIGHT = {ConflictSeverity.LOW: 0.25, ConflictSeverity.MEDIUM: 0.5, ConflictSeverity.HIGH: 1.0}
_MAX_CONFLICT_WEIGHT = 4.0  # exactly 4 conflict rules, each firing at most once - see module docstring


class UncertaintyEngine:
    """Configuration is explicit and constructor-injected - PROJECT
    DEVELOPMENT HEURISTIC thresholds, never a hardcoded bank policy
    (Phase 2E §18)."""

    def __init__(
        self,
        *,
        low_uncertainty_max: float = 0.33,
        high_uncertainty_min: float = 0.66,
        min_coverage_for_sufficiency: float = 0.5,
        quality_disparity_min_related: int = 10,
    ) -> None:
        if not (0.0 <= low_uncertainty_max < high_uncertainty_min <= 1.0):
            raise ValueError("require 0.0 <= low_uncertainty_max < high_uncertainty_min <= 1.0")
        self._low_max = low_uncertainty_max
        self._high_min = high_uncertainty_min
        self._min_coverage = min_coverage_for_sufficiency
        self._quality_disparity_min_related = quality_disparity_min_related

    def assess(self, snapshot: InvestigationSnapshot) -> UncertaintyAssessment:
        by_type = self._evidence_by_type(snapshot)

        coverage = self._evidence_coverage(snapshot)
        missing = self._missing_evidence(snapshot, by_type)
        conflicts = self._detect_conflicts(snapshot, by_type)
        conflict_score = self._signal_conflict(conflicts)
        quality = self._signal_quality(by_type)
        completeness = self._data_completeness(snapshot)

        factors = [
            UncertaintyFactor(
                factor="evidence_coverage", value=coverage,
                source="investigation_snapshot.tool_results",
                detail=f"{round(coverage * _TOTAL_PLANNED_SOURCES)}/{_TOTAL_PLANNED_SOURCES} "
                       "planned sources completed (SUCCESS or EMPTY).",
            ),
            UncertaintyFactor(
                factor="signal_quality", value=quality,
                source="evidence_bundle.evidence[*].quality",
                detail="Average of LOW=0.33/MEDIUM=0.66/HIGH=1.0 over SUCCESS-status, rated evidence only."
                if quality is not None else "No SUCCESS-status evidence item carried a rated quality.",
            ),
            UncertaintyFactor(
                factor="signal_conflict", value=conflict_score,
                source="uncertainty_engine.conflicting_evidence",
                detail=f"{len(conflicts)} conflict(s) detected." if conflicts else "No conflicts detected.",
            ),
            UncertaintyFactor(
                factor="data_completeness", value=completeness,
                source="evidence_bundle.evidence[transaction_context].metrics",
                detail=f"{_CONTEXT_REQUIRED_FIELDS} populated fraction." if completeness is not None
                else "transaction_context did not succeed - no basis to assess.",
            ),
        ]

        if snapshot.status == InvestigationStatus.FAILED:
            overall = None
            level = UncertaintyLevel.UNKNOWN
            sufficient = False
        else:
            overall = self._overall_uncertainty(coverage, quality, conflict_score, completeness)
            level = self._uncertainty_level(overall)
            sufficient = self._sufficient_for_next_stage(snapshot, coverage, conflicts)

        rationale = self._build_rationale(
            snapshot, coverage, quality, conflict_score, completeness, missing, conflicts, level
        )

        return UncertaintyAssessment(
            investigation_id=snapshot.investigation_id,
            transaction_id=snapshot.transaction_id,
            evidence_coverage=coverage,
            signal_quality=quality,
            signal_conflict=conflict_score,
            data_completeness=completeness,
            overall_uncertainty=overall,
            uncertainty_level=level,
            missing_evidence=missing,
            conflicting_evidence=conflicts,
            factors=factors,
            sufficient_for_next_stage=sufficient,
            rationale=rationale,
        )

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _evidence_by_type(snapshot: InvestigationSnapshot) -> dict[EvidenceType, Evidence]:
        bundle = snapshot.evidence_bundle
        if bundle is None:
            return {}
        return {e.evidence_type: e for e in bundle.evidence}

    @staticmethod
    def _evidence_coverage(snapshot: InvestigationSnapshot) -> float:
        covered = sum(
            1
            for name in INVESTIGATION_PLAN
            if name in snapshot.tool_results
            and snapshot.tool_results[name].status in (QueryStatus.SUCCESS, QueryStatus.EMPTY)
        )
        return covered / _TOTAL_PLANNED_SOURCES

    @staticmethod
    def _signal_quality(by_type: dict[EvidenceType, Evidence]) -> float | None:
        scores = [
            _QUALITY_SCORE[e.quality]
            for e in by_type.values()
            if e.status == QueryStatus.SUCCESS and e.quality in _QUALITY_SCORE
        ]
        if not scores:
            return None
        return sum(scores) / len(scores)

    @staticmethod
    def _data_completeness(snapshot: InvestigationSnapshot) -> float | None:
        ctx = snapshot.tool_results.get("get_transaction_context")
        if ctx is None or ctx.status != QueryStatus.SUCCESS or ctx.evidence is None:
            return None
        metrics = ctx.evidence.metrics
        populated = sum(1 for field in _CONTEXT_REQUIRED_FIELDS if metrics.get(field) is not None)
        return populated / len(_CONTEXT_REQUIRED_FIELDS)

    @staticmethod
    def _missing_evidence(
        snapshot: InvestigationSnapshot, by_type: dict[EvidenceType, Evidence]
    ) -> list[MissingEvidence]:
        missing: list[MissingEvidence] = []
        for name in INVESTIGATION_PLAN:
            result = snapshot.tool_results.get(name)
            if result is None:
                missing.append(
                    MissingEvidence(
                        evidence_type=name,
                        reason=MissingEvidenceReason.NOT_INVESTIGATED,
                        source_status=None,
                        impact=f"{name} did not run - this evidence area is completely unassessed.",
                    )
                )
            elif result.status == QueryStatus.ERROR:
                error_type = result.error.error_type.value if result.error else "UNKNOWN"
                missing.append(
                    MissingEvidence(
                        evidence_type=name,
                        reason=MissingEvidenceReason.QUERY_ERROR,
                        source_status=QueryStatus.ERROR,
                        impact=f"{name} failed ({error_type}) - whether related activity exists "
                        "here is unknown, not confirmed absent.",
                    )
                )

        ctx = by_type.get(EvidenceType.TRANSACTION_CONTEXT)
        if ctx is not None and ctx.status == QueryStatus.SUCCESS:
            for field in _CONTEXT_REQUIRED_FIELDS:
                if ctx.metrics.get(field) is None:
                    missing.append(
                        MissingEvidence(
                            evidence_type=f"transaction_context.{field}",
                            reason=MissingEvidenceReason.DATA_MISSING,
                            source_status=QueryStatus.SUCCESS,
                            impact=f"transaction_context succeeded, but the underlying '{field}' "
                            "field was null - a real data gap, not a query failure.",
                        )
                    )
        return missing

    # ------------------------------------------------------------ conflicts

    def _detect_conflicts(
        self, snapshot: InvestigationSnapshot, by_type: dict[EvidenceType, Evidence]
    ) -> list[EvidenceConflict]:
        detectors = (
            self._detect_signal_disagreement,
            self._detect_data_inconsistency,
            lambda by_type: self._detect_quality_disparity(by_type),
            lambda by_type: self._detect_missing_context(snapshot, by_type),
        )
        conflicts = [c for detector in detectors if (c := detector(by_type)) is not None]
        return conflicts

    @staticmethod
    def _detect_signal_disagreement(by_type: dict[EvidenceType, Evidence]) -> EvidenceConflict | None:
        ctx = by_type.get(EvidenceType.TRANSACTION_CONTEXT)
        if ctx is None or ctx.status != QueryStatus.SUCCESS:
            return None

        checks = (
            ("has_card", EvidenceType.SHARED_CARD),
            ("has_address", EvidenceType.SHARED_ADDRESS),
            ("has_device", EvidenceType.SHARED_DEVICE),
            ("has_purchaser_email", EvidenceType.SHARED_EMAIL_DOMAIN),
        )
        mismatches: list[str] = []
        evidence_ids = [ctx.evidence_id]

        for ctx_field, ev_type in checks:
            shared = by_type.get(ev_type)
            if shared is None or shared.status not in (QueryStatus.SUCCESS, QueryStatus.EMPTY):
                continue
            if ev_type == EvidenceType.SHARED_EMAIL_DOMAIN:
                linked = shared.metrics.get("purchaser_domain") is not None
            else:
                linked = shared.provenance.entity_id is not None
            ctx_says = ctx.metrics.get(ctx_field)
            if ctx_says is None or bool(ctx_says) == linked:
                continue
            mismatches.append(f"transaction_context.{ctx_field}={ctx_says} but {ev_type.value} linked={linked}")
            evidence_ids.append(shared.evidence_id)

        if not mismatches:
            return None
        return EvidenceConflict(
            conflict_id=f"{ctx.transaction_id}:signal_disagreement",
            evidence_ids=evidence_ids,
            conflict_type=ConflictType.SIGNAL_DISAGREEMENT,
            description="transaction_context and a dedicated shared-entity query disagree about "
            "whether an entity is linked to this transaction: " + "; ".join(mismatches),
            severity=ConflictSeverity.HIGH,
        )

    @staticmethod
    def _detect_data_inconsistency(by_type: dict[EvidenceType, Evidence]) -> EvidenceConflict | None:
        network = by_type.get(EvidenceType.NETWORK_PATTERN)
        if network is None or network.status not in (QueryStatus.SUCCESS, QueryStatus.EMPTY):
            return None
        breakdown = network.metrics.get("related_by_entity_type", {})

        checks = (
            (EvidenceType.SHARED_CARD, "Card", "related_transaction_count"),
            (EvidenceType.SHARED_ADDRESS, "Address", "related_transaction_count"),
            (EvidenceType.SHARED_EMAIL_DOMAIN, "EmailDomain_purchaser", "purchaser_related_count"),
        )
        mismatches: list[str] = []
        evidence_ids = [network.evidence_id]

        for ev_type, breakdown_key, metric_key in checks:
            dedicated = by_type.get(ev_type)
            if dedicated is None or dedicated.status not in (QueryStatus.SUCCESS, QueryStatus.EMPTY):
                continue
            dedicated_count = dedicated.metrics.get(metric_key, 0)
            network_count = breakdown.get(breakdown_key, 0)
            if dedicated_count != network_count:
                mismatches.append(f"{breakdown_key}: network={network_count} vs dedicated={dedicated_count}")
                evidence_ids.append(dedicated.evidence_id)

        if not mismatches:
            return None
        return EvidenceConflict(
            conflict_id=f"{network.transaction_id}:data_inconsistency",
            evidence_ids=evidence_ids,
            conflict_type=ConflictType.DATA_INCONSISTENCY,
            description="network_pattern's per-entity-type breakdown disagrees with the dedicated "
            "shared-entity query count for the same transaction, which should always match "
            "exactly (both derive from the same graph edges): " + "; ".join(mismatches),
            severity=ConflictSeverity.HIGH,
        )

    def _detect_quality_disparity(self, by_type: dict[EvidenceType, Evidence]) -> EvidenceConflict | None:
        network = by_type.get(EvidenceType.NETWORK_PATTERN)
        if network is None or network.status != QueryStatus.SUCCESS:
            return None
        if network.quality != SignalQuality.LOW:
            return None
        total = network.metrics.get("total_related_not_deduplicated", 0)
        if total < self._quality_disparity_min_related:
            return None
        return EvidenceConflict(
            conflict_id=f"{network.transaction_id}:quality_disparity",
            evidence_ids=[network.evidence_id],
            conflict_type=ConflictType.QUALITY_DISPARITY,
            description=(
                f"The transaction's apparent network reaches {total} related transaction-slots, "
                "but network_pattern's own quality rating is LOW: the size is driven "
                "predominantly by low-quality linking types (Address and/or EmailDomain), not "
                "higher-quality ones (Card/Device). The raw count should not be read as strong "
                "evidentiary weight."
            ),
            severity=ConflictSeverity.MEDIUM,
        )

    @staticmethod
    def _detect_missing_context(
        snapshot: InvestigationSnapshot, by_type: dict[EvidenceType, Evidence]
    ) -> EvidenceConflict | None:
        missing_types: list[str] = []
        present_material: list[str] = []
        for name in INVESTIGATION_PLAN:
            if name == "get_transaction_context":
                continue
            result = snapshot.tool_results.get(name)
            if result is None or result.status == QueryStatus.ERROR:
                missing_types.append(name)
            elif result.status == QueryStatus.SUCCESS:
                present_material.append(name)

        if not missing_types or not present_material:
            return None

        evidence_ids = [
            snapshot.tool_results[name].evidence.evidence_id
            for name in present_material
            if snapshot.tool_results[name].evidence is not None
        ]
        return EvidenceConflict(
            conflict_id=f"{snapshot.transaction_id}:missing_context",
            evidence_ids=evidence_ids,
            conflict_type=ConflictType.MISSING_CONTEXT,
            description=(
                f"{len(missing_types)} evidence source(s) unavailable ({', '.join(missing_types)}) "
                f"while {len(present_material)} other source(s) produced findings "
                f"({', '.join(present_material)}) - the investigation's picture is uneven, not "
                "necessarily wrong."
            ),
            severity=ConflictSeverity.LOW,
        )

    @staticmethod
    def _signal_conflict(conflicts: list[EvidenceConflict]) -> float:
        if not conflicts:
            return 0.0
        total = sum(_SEVERITY_WEIGHT[c.severity] for c in conflicts)
        return min(1.0, total / _MAX_CONFLICT_WEIGHT)

    @staticmethod
    def _overall_uncertainty(
        coverage: float, quality: float | None, conflict: float, completeness: float | None
    ) -> float:
        components = [1.0 - coverage, conflict]
        if quality is not None:
            components.append(1.0 - quality)
        if completeness is not None:
            components.append(1.0 - completeness)
        return sum(components) / len(components)

    def _uncertainty_level(self, overall: float | None) -> UncertaintyLevel:
        if overall is None:
            return UncertaintyLevel.UNKNOWN
        if overall <= self._low_max:
            return UncertaintyLevel.LOW
        if overall >= self._high_min:
            return UncertaintyLevel.HIGH
        return UncertaintyLevel.MEDIUM

    def _sufficient_for_next_stage(
        self, snapshot: InvestigationSnapshot, coverage: float, conflicts: list[EvidenceConflict]
    ) -> bool:
        if snapshot.status == InvestigationStatus.FAILED:
            return False
        if coverage < self._min_coverage:
            return False
        return not any(c.severity == ConflictSeverity.HIGH for c in conflicts)

    # ------------------------------------------------------------ rationale

    @staticmethod
    def _build_rationale(
        snapshot: InvestigationSnapshot,
        coverage: float,
        quality: float | None,
        conflict: float,
        completeness: float | None,
        missing: list[MissingEvidence],
        conflicts: list[EvidenceConflict],
        level: UncertaintyLevel,
    ) -> str:
        if snapshot.status == InvestigationStatus.FAILED:
            return (
                "Investigation uncertainty is UNKNOWN: transaction context could not be "
                "established, so no further evidence assessment is possible."
            )

        clauses = [
            (
                f"Evidence coverage is {coverage:.0%} "
                f"({round(coverage * _TOTAL_PLANNED_SOURCES)}/{_TOTAL_PLANNED_SOURCES} planned sources completed)."
            )
        ]
        clauses.append(
            f"Signal quality of obtained evidence averages {quality:.2f} on this project's "
            "internal 0-1 scale (not a fraud score)."
            if quality is not None
            else "Signal quality could not be assessed - no evidence item carried a rated quality."
        )
        clauses.append(
            f"Transaction-context data completeness is {completeness:.0%}."
            if completeness is not None
            else "Data completeness could not be assessed - transaction context did not succeed."
        )
        if conflicts:
            clauses.append(
                f"{len(conflicts)} evidence conflict(s) detected: "
                + "; ".join(f"{c.conflict_type.value} ({c.severity.value})" for c in conflicts)
                + "."
            )
        else:
            clauses.append("No evidence conflicts detected.")
        if missing:
            clauses.append(
                f"{len(missing)} evidence gap(s) noted: "
                + ", ".join(f"{m.evidence_type} ({m.reason.value})" for m in missing)
                + "."
            )
        clauses.append(f"Overall investigation uncertainty level: {level.value}.")
        return " ".join(clauses)
