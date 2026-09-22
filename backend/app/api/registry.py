"""Phase 2K - a thin, process-local investigation registry.

This exists only because `CaseRecord` (Phase 2G, unmodified) deliberately
stores compact references (`evidence_ids`, `evidence_types`) rather than
the full `InvestigationSnapshot`/`EvidenceBundle`/`InvestigationContext`
(see `app/case/models.py`'s module docstring). Those full, richer objects
are still produced by every `AgentOrchestrator.run()` call
(`AgentInvestigationResult.evidence`/`.context`) - this registry is
nothing more than an in-memory cache of that already-computed output, so
`GET /cases/{case_id}/evidence` and `GET /cases/{case_id}/context` can
serve it back without recomputing anything or querying TigerGraph again.

**Explicit limitation** (documented, not hidden): this is a plain
in-process dict. It is empty on process start, is never persisted, and
only ever contains investigations that ran through *this* API process.
A case retrieved from `CaseStore` that this process didn't itself
investigate will have a `CaseRecord` (full detail, via `GET
/cases/{case_id}`) but no cached `AgentInvestigationResult` - the
evidence/context endpoints degrade to the reduced, CaseRecord-derived
fields in that situation and say so in the response, rather than
fabricating the missing detail. This is deliberately not a "second case
storage system" - it stores nothing `CaseManager`/`CaseStore` doesn't
already own the authoritative copy of; it is purely a serving-side cache
of transient objects that were never persisted anywhere at all.
"""

from __future__ import annotations

import threading

from app.agent.state import AgentInvestigationResult


class InvestigationRegistry:
    def __init__(self) -> None:
        self._results: dict[str, AgentInvestigationResult] = {}
        self._lock = threading.Lock()

    def put(self, investigation_id: str, result: AgentInvestigationResult) -> None:
        with self._lock:
            self._results[investigation_id] = result

    def get(self, investigation_id: str) -> AgentInvestigationResult | None:
        with self._lock:
            return self._results.get(investigation_id)
