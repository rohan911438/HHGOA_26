"""Phase 2H - OPTIONAL real-LLM integration test.

This is deliberately NOT part of the required deterministic suite
(Phase 2H §24): it is marked `llm` (see pyproject.toml's marker list,
already declared: "llm: requires a live LLM API key") and skips cleanly
whenever `Settings.llm_configured` is False - which it is by default in
this development environment (no OPENAI_API_KEY/OPENAI_MODEL set).

Run explicitly with `pytest -m llm` once real credentials are
configured. Never let this test's availability (or a real LLM
provider's instability) affect whether the deterministic backend suite
is considered healthy - it is intentionally excluded from the default
`pytest` collection alongside `tigergraph`-marked tests (see
`pytest -m "not tigergraph"` / the project's default CI run convention).
"""

from __future__ import annotations

import pytest

from app.agent import AgentOrchestrator, AgentStatus, LLMNotConfiguredError, OpenAIChatClient
from app.case import InMemoryCaseStore
from app.config import get_settings
from app.tigergraph.client import get_client
from app.tigergraph.diagnostics import all_passed, run_checks

pytestmark = [pytest.mark.llm, pytest.mark.tigergraph]

KNOWN_TXN = "2987937"


@pytest.fixture(scope="module")
def client():
    settings = get_settings()
    if not settings.llm_configured:
        pytest.skip("No real LLM configured (OPENAI_API_KEY/OPENAI_MODEL not set) - this is expected by default.")
    if not settings.tg_configured:
        pytest.skip("TigerGraph not configured - set TG_HOST/TG_GRAPHNAME/TG_SECRET in .env")
    c = get_client(settings)
    results = run_checks(settings, c)
    if not all_passed(results):
        detail = next((r.detail for r in results if r.ok is False), "unknown failure")
        pytest.skip(f"TigerGraph is not currently reachable/healthy - {detail}")
    return c


def test_real_llm_produces_a_valid_structured_decision(client):
    """Does not assert specific LLM wording - only that the structured,
    deterministic fields remain correct regardless of what the model
    said (Phase 2H §22)."""
    llm = OpenAIChatClient()
    orchestrator = AgentOrchestrator(client, InMemoryCaseStore(), llm, max_iterations=3)
    result = orchestrator.run(KNOWN_TXN)

    assert result.investigation_status in (AgentStatus.COMPLETED, AgentStatus.LIMIT_REACHED)
    assert result.policy_decision is not None
    assert result.final_explanation is not None
    assert len(result.final_explanation) > 0


def test_llm_not_configured_fails_clearly_not_silently():
    """Offline-safe (needs no credential or network call): confirms the
    real client's own guard, not a live call."""
    from app.config import Settings

    unconfigured = Settings(_env_file=None, openai_api_key="", openai_model="")
    with pytest.raises(LLMNotConfiguredError):
        OpenAIChatClient(unconfigured)
