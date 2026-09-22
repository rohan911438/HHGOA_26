"""Phase 2J.1/2J.2 - discovers (or fails to discover) the official
HHGoa 20-case benchmark material, and refuses to substitute anything
else for it.

This module is deliberately small: the official file format is unknown
until real files exist to inspect (`docs/development-fallback-analysis.md`
§1), so a real parser cannot be written yet without guessing a schema -
which the project's instructions explicitly forbid. What *can* be built
now, and is: a loader that checks the configured official-data paths,
reports exactly what it finds, and raises rather than falling back to
the IEEE-CIS development dataset when nothing is there.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.benchmark.models import BenchmarkCase
from app.config import Settings, get_settings


class DiscoveryReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    official_dataset_available: bool
    checked_paths: dict[str, str] = Field(default_factory=dict)  # label -> resolved path
    path_exists: dict[str, bool] = Field(default_factory=dict)  # label -> exists
    case_files_found: list[str] = Field(default_factory=list)
    policy_files_found: list[str] = Field(default_factory=list)
    benchmark_files_found: list[str] = Field(default_factory=list)
    dataset_root_contents: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class OfficialBenchmarkUnavailable(RuntimeError):
    """Raised by `load_official_benchmark_cases()` when discovery finds
    nothing. Callers must not catch this and substitute the IEEE-CIS
    fallback - that is exactly the silent-substitution this phase's
    instructions prohibit."""


def discover_official_benchmark(settings: Settings | None = None) -> DiscoveryReport:
    """Re-checks the configured DATASET_ROOT/CASES_PATH/POLICIES_PATH/
    BENCHMARK_PATH for official HHGoa material. Read-only - writes
    nothing, downloads nothing."""
    settings = settings or get_settings()

    dataset_root = settings.resolve(settings.dataset_root)
    cases_path = settings.resolve(settings.cases_path)
    policies_path = settings.resolve(settings.policies_path)
    benchmark_path = settings.resolve(settings.benchmark_path)

    checked_paths = {
        "dataset_root": str(dataset_root),
        "cases_path": str(cases_path),
        "policies_path": str(policies_path),
        "benchmark_path": str(benchmark_path),
    }
    path_exists = {
        "dataset_root": dataset_root.exists(),
        "cases_path": cases_path.exists(),
        "policies_path": policies_path.exists(),
        "benchmark_path": benchmark_path.exists(),
    }

    case_files = _list_files(cases_path)
    policy_files = _list_files(policies_path)
    benchmark_files = _list_files(benchmark_path)

    dataset_root_contents = sorted(p.name for p in dataset_root.iterdir()) if dataset_root.exists() else []

    notes: list[str] = []
    if dataset_root_contents:
        notes.append(f"dataset_root contains: {dataset_root_contents}")
    else:
        notes.append("dataset_root does not exist or is empty")

    available = bool(case_files or policy_files or benchmark_files)
    if not available:
        notes.append(
            "OFFICIAL BENCHMARK DATA UNAVAILABLE - CASES_PATH, POLICIES_PATH "
            "and BENCHMARK_PATH are all absent or empty. Only the IEEE-CIS "
            "development-fallback CSVs (train_transaction.csv, "
            "train_identity.csv) are present under dataset_root - see "
            "docs/phase-1-report.md §1 (original search record) and "
            "docs/phase-2-benchmark-report.md (this phase's re-check)."
        )

    return DiscoveryReport(
        official_dataset_available=available,
        checked_paths=checked_paths,
        path_exists=path_exists,
        case_files_found=case_files,
        policy_files_found=policy_files,
        benchmark_files_found=benchmark_files,
        dataset_root_contents=dataset_root_contents,
        notes=notes,
    )


def _list_files(path: Path) -> list[str]:
    if not path.exists():
        return []
    return sorted(str(p) for p in path.glob("**/*") if p.is_file())


def load_official_benchmark_cases(settings: Settings | None = None) -> list[BenchmarkCase]:
    """Would parse real official case files into `BenchmarkCase` records.

    Raises `OfficialBenchmarkUnavailable` instead of fabricating or
    silently substituting the IEEE-CIS fallback when discovery finds
    nothing - per the explicit instruction never to manufacture the 20
    benchmark cases. If files are ever found, this still raises
    `NotImplementedError` rather than guessing their format: implement
    the real parser against the real file layout when it exists, not
    before.
    """
    report = discover_official_benchmark(settings)
    if not report.official_dataset_available:
        raise OfficialBenchmarkUnavailable(
            "OFFICIAL BENCHMARK DATA UNAVAILABLE: no files found under "
            "CASES_PATH/POLICIES_PATH/BENCHMARK_PATH. Refusing to "
            "substitute the IEEE-CIS development fallback as benchmark "
            "cases. Run discover_official_benchmark() for the full report."
        )
    raise NotImplementedError(
        "Benchmark-shaped files were found but no official-format parser "
        "exists yet - the format was never inspected/documented. Write "
        "the real parser against these actual files before calling this "
        "function; do not guess the schema."
    )
