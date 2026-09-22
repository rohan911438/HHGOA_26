"""Phase 2G - the case persistence boundary.

`CaseStore` is an abstract interface so `CaseManager` and `CaseMemory`
never depend on a concrete storage technology. This phase ships only
`InMemoryCaseStore` (development/tests) - per the brief, a TigerGraph-
backed (or any other persistent) implementation is deliberately NOT
built this phase, but the boundary is designed so one could be added
later (`class TigerGraphCaseStore(CaseStore): ...`) without changing
`CaseManager`/`CaseMemory` logic at all - they only ever call methods on
this abstract interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.case.models import CaseRecord


class CaseStore(ABC):
    @abstractmethod
    def save(self, case: CaseRecord) -> None: ...

    @abstractmethod
    def get(self, case_id: str) -> CaseRecord | None: ...

    @abstractmethod
    def list_all(self) -> list[CaseRecord]: ...

    @abstractmethod
    def list_by_transaction(self, transaction_id: str) -> list[CaseRecord]: ...


class InMemoryCaseStore(CaseStore):
    """Development/test storage. Not persistent across process restarts -
    that is an explicit, documented Phase 2G limitation, not an oversight."""

    def __init__(self) -> None:
        self._cases: dict[str, CaseRecord] = {}

    def save(self, case: CaseRecord) -> None:
        self._cases[case.case_id] = case

    def get(self, case_id: str) -> CaseRecord | None:
        return self._cases.get(case_id)

    def list_all(self) -> list[CaseRecord]:
        # Sorted by case_id - deterministic regardless of dict insertion order.
        return [self._cases[k] for k in sorted(self._cases)]

    def list_by_transaction(self, transaction_id: str) -> list[CaseRecord]:
        return sorted(
            (c for c in self._cases.values() if c.transaction_id == transaction_id),
            key=lambda c: c.case_id,
        )
