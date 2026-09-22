"""Phase 2E - live pipeline test: the uncertainty engine against a real
InvestigationSnapshot from the loaded HHGOA_FRAUD graph.

    InvestigationService (Phase 2D)
            v
    InvestigationSnapshot
            v
    UncertaintyEngine.assess()          <- pure computation, no TigerGraph
            v
    UncertaintyAssessment

Only the snapshot-producing half of this test needs TigerGraph - the
engine itself never touches it (see
tests/unit/test_uncertainty_engine.py::TestArchitecturalBoundary for the
offline, TigerGraph-free proof of that). This file is marked `tigergraph`
because building the snapshot to assess requires a live connection, same
convention as the other live test modules in this project.

Skips (does not fail) when TigerGraph is not currently reachable, using
this project's own diagnostics.
"""

from __future__ import annotations

import time

import pytest

from app.config import get_settings
from app.investigation import InvestigationService, build_default_registry
from app.investigation.snapshot import InvestigationStatus
from app.tigergraph.client import get_client
from app.tigergraph.diagnostics import all_passed, run_checks
from app.uncertainty import ConflictType, UncertaintyEngine

pytestmark = pytest.mark.tigergraph

KNOWN_TXN = "2987937"


@pytest.fixture(scope="module")
def client():
    settings = get_settings()
    if not settings.tg_configured:
        pytest.skip("TigerGraph not configured - set TG_HOST/TG_GRAPHNAME/TG_SECRET in .env")
    c = get_client(settings)
    results = run_checks(settings, c)
    if not all_passed(results):
        detail = next((r.detail for r in results if r.ok is False), "unknown failure")
        pytest.skip(f"TigerGraph is not currently reachable/healthy - {detail}")
    return c


@pytest.fixture(scope="module")
def snapshot(client):
    service = InvestigationService(build_default_registry())
    return service.investigate_transaction(KNOWN_TXN, client)


@pytest.fixture
def engine() -> UncertaintyEngine:
    return UncertaintyEngine()


class TestLiveUncertaintyPipeline:
    def test_snapshot_is_completed(self, snapshot):
        assert snapshot.status == InvestigationStatus.COMPLETED

    def test_full_coverage_on_the_known_healthy_fixture(self, snapshot, engine):
        assessment = engine.assess(snapshot)
        assert assessment.evidence_coverage == 1.0
        assert assessment.missing_evidence == []

    def test_address_low_quality_is_reflected_in_the_aggregate(self, snapshot, engine):
        assessment = engine.assess(snapshot)
        # Address is SUCCESS/LOW and Email is SUCCESS/LOW; Card is
        # SUCCESS/MEDIUM; Device is EMPTY (excluded); network_pattern's own
        # quality is LOW too (Address dominates the fan-out, per the
        # documented Phase 2A/2B finding) - the aggregate must sit below
        # the MEDIUM ceiling (0.66), not be pulled up by the raw address
        # count.
        assert assessment.signal_quality is not None
        assert assessment.signal_quality < 0.66

    def test_device_empty_does_not_appear_as_missing_evidence(self, snapshot, engine):
        assessment = engine.assess(snapshot)
        assert not any(m.evidence_type == "find_shared_device_activity" for m in assessment.missing_evidence)

    def test_quality_disparity_conflict_is_detected_for_this_known_fixture(self, snapshot, engine):
        # This is the exact, real, documented finding from Phase 2A/2B:
        # Address (312 related) dominates a LOW-quality network_pattern.
        assessment = engine.assess(snapshot)
        types = {c.conflict_type for c in assessment.conflicting_evidence}
        assert ConflictType.QUALITY_DISPARITY in types

    def test_no_data_inconsistency_on_a_healthy_graph(self, snapshot, engine):
        # The network breakdown must agree exactly with the dedicated
        # queries against the real, loaded graph - re-verifying the same
        # invariant Phase 2A/2B/2C/2D already assert, this time through
        # the uncertainty engine's own conflict detector.
        assessment = engine.assess(snapshot)
        types = {c.conflict_type for c in assessment.conflicting_evidence}
        assert ConflictType.DATA_INCONSISTENCY not in types

    def test_no_dataset_label_leakage_on_a_real_snapshot(self, client, engine):
        service = InvestigationService(build_default_registry())
        snap = service.investigate_transaction(KNOWN_TXN, client)
        real_label = snap.evidence_bundle.dataset_risk_score

        assessment_real = engine.assess(snap)

        # Rebuild an identical snapshot with the opposite label and
        # confirm the uncertainty assessment does not move.
        import copy

        flipped = copy.deepcopy(snap)
        flipped_bundle = flipped.evidence_bundle.model_copy(
            update={"dataset_risk_score": 0.0 if real_label == 1.0 else 1.0}
        )
        flipped = flipped.model_copy(update={"evidence_bundle": flipped_bundle})

        assessment_flipped = engine.assess(flipped)
        assert assessment_real.model_dump(mode="json") == assessment_flipped.model_dump(mode="json")

    def test_no_fraud_verdict_in_a_real_assessment(self, snapshot, engine):
        assessment = engine.assess(snapshot)
        dumped = assessment.model_dump(mode="json")
        assert "fraud_probability" not in dumped
        assert "final_fraud_score" not in dumped
        assert "fraud_verdict" not in dumped

    def test_rationale_mentions_the_actual_evidence_not_generic_text(self, snapshot, engine):
        assessment = engine.assess(snapshot)
        assert "coverage" in assessment.rationale.lower()
        assert assessment.uncertainty_level.value in assessment.rationale

    def test_assessment_is_json_serializable(self, snapshot, engine):
        import json

        json.dumps(engine.assess(snapshot).model_dump(mode="json"))

    def test_engine_runtime_is_orders_of_magnitude_faster_than_the_investigation(self, snapshot, engine):
        started = time.perf_counter()
        for _ in range(20):
            engine.assess(snapshot)
        avg_ms = (time.perf_counter() - started) / 20 * 1000
        print(f"\nUncertaintyEngine.assess() avg latency over 20 runs: {avg_ms:.3f} ms")
        assert avg_ms < 50  # investigation itself measured in seconds (Phase 2D docs)
