"""Phase 2J - the official HHGoa benchmark adapter.

    Official benchmark case (if found)
            v
    discovery.discover_official_benchmark() / load_official_benchmark_cases()
            v
    BenchmarkCase -> AgentOrchestrator.run() -> AgentInvestigationResult
            v
    BenchmarkResult

STATUS: the official HHGOA_IEEE 20-case benchmark has not been found in
this repository or development environment - see
docs/phase-2-benchmark-report.md. `load_official_benchmark_cases()`
raises `OfficialBenchmarkUnavailable` rather than substituting the
IEEE-CIS development fallback. `runner.run_infrastructure_validation()`
is the only thing this package currently executes - a diagnostic,
clearly-labeled pipeline-wiring check, never a benchmark score.
"""

from __future__ import annotations

from app.benchmark.discovery import (
    DiscoveryReport,
    OfficialBenchmarkUnavailable,
    discover_official_benchmark,
    load_official_benchmark_cases,
)
from app.benchmark.models import (
    BenchmarkCase,
    BenchmarkExpectedAnswer,
    BenchmarkResult,
    ComparisonCategory,
    ComparisonOutcome,
)
from app.benchmark.runner import run_infrastructure_validation

__all__ = [
    "BenchmarkCase",
    "BenchmarkExpectedAnswer",
    "BenchmarkResult",
    "ComparisonCategory",
    "ComparisonOutcome",
    "DiscoveryReport",
    "OfficialBenchmarkUnavailable",
    "discover_official_benchmark",
    "load_official_benchmark_cases",
    "run_infrastructure_validation",
]
