"""Phase 2J.1 - unit tests for official-benchmark discovery.

No TigerGraph, no LLM, no network - these tests only exercise the
filesystem-discovery logic in app.benchmark.discovery. They deliberately
assert the CURRENT repository state (official data absent) and, more
importantly, that discovery never fabricates or substitutes the IEEE-CIS
fallback when it finds nothing - the entire point of this module.
"""

from __future__ import annotations

import pytest

from app.benchmark.discovery import (
    OfficialBenchmarkUnavailable,
    discover_official_benchmark,
    load_official_benchmark_cases,
)
from app.config import Settings


def _settings(tmp_path, **overrides) -> Settings:
    root = tmp_path / "hhgoa"
    root.mkdir()
    defaults = {
        "dataset_root": str(root),
        "cases_path": str(root / "cases"),
        "policies_path": str(root / "policies"),
        "benchmark_path": str(root / "benchmark"),
    }
    defaults.update(overrides)
    return Settings(**defaults)


class TestDiscoveryReportsAbsence:
    def test_current_repository_has_no_official_benchmark(self):
        # Uses the real, configured settings - this is the actual
        # repository/environment state this phase must report honestly.
        report = discover_official_benchmark()
        assert report.official_dataset_available is False
        assert report.case_files_found == []
        assert report.policy_files_found == []
        assert report.benchmark_files_found == []
        assert any("OFFICIAL BENCHMARK DATA UNAVAILABLE" in note for note in report.notes)

    def test_empty_configured_paths_report_unavailable(self, tmp_path):
        settings = _settings(tmp_path)
        report = discover_official_benchmark(settings)
        assert report.official_dataset_available is False
        assert report.path_exists == {
            "dataset_root": True,
            "cases_path": False,
            "policies_path": False,
            "benchmark_path": False,
        }

    def test_a_file_under_cases_path_flips_availability(self, tmp_path):
        settings = _settings(tmp_path)
        cases_dir = settings.resolve(settings.cases_path)
        cases_dir.mkdir(parents=True)
        (cases_dir / "case_001.json").write_text("{}", encoding="utf-8")

        report = discover_official_benchmark(settings)
        assert report.official_dataset_available is True
        assert len(report.case_files_found) == 1


class TestLoaderNeverFabricates:
    def test_load_raises_when_unavailable_rather_than_returning_fallback_data(self, tmp_path):
        settings = _settings(tmp_path)
        with pytest.raises(OfficialBenchmarkUnavailable):
            load_official_benchmark_cases(settings)

    def test_load_does_not_implement_a_guessed_format_when_files_exist(self, tmp_path):
        settings = _settings(tmp_path)
        cases_dir = settings.resolve(settings.cases_path)
        cases_dir.mkdir(parents=True)
        (cases_dir / "case_001.json").write_text("{}", encoding="utf-8")

        with pytest.raises(NotImplementedError):
            load_official_benchmark_cases(settings)
