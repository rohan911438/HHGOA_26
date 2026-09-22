"""Phase 2K - FastAPI dependency providers.

Every external or shared resource the API needs is created here, behind
a `Depends`-injected function - never inline in a route handler. This is
what lets tests inject `FakeLLMClient` / a fake TigerGraph client /
an in-memory `CaseStore` via `app.dependency_overrides`, without a live
TigerGraph connection or an OpenAI key (see `tests/api/`).

Nothing here opens a TigerGraph connection at import time or at
`create_app()` time - `TigerGraphClient` (Phase 1, unmodified) is
already a lazy wrapper that only connects on first real call, and
`get_tigergraph_client` below simply reuses the project's existing
`app.tigergraph.client.get_client` singleton factory, cached on
`app.state` for this process's lifetime.
"""

from __future__ import annotations

from fastapi import Depends, Request

from app.agent import AgentOrchestrator, LLMClient, LLMNotConfiguredError, OpenAIChatClient
from app.api.errors import DependencyUnavailableError
from app.api.registry import InvestigationRegistry
from app.case import CaseManager, CaseMemory, CaseStore, InMemoryCaseStore
from app.config import Settings, get_settings
from app.investigation import InvestigationService, build_default_registry
from app.tigergraph.client import TigerGraphClient, get_client


def get_settings_dependency() -> Settings:
    return get_settings()


def get_case_store(request: Request) -> CaseStore:
    """One `InMemoryCaseStore` per app instance, created lazily and
    shared for the process's lifetime - the same store `CaseManager` and
    `CaseMemory` below both wrap, so a case created by `POST
    /investigations` is immediately visible to `GET /cases/{case_id}`.

    Phase 2K limitation, stated plainly: this is `InMemoryCaseStore`
    (Phase 2G's only shipped `CaseStore` implementation) - cases do not
    survive an API process restart. No new database is introduced for
    this phase; see docs/phase-2-api.md "Known limitations".
    """
    if not hasattr(request.app.state, "case_store"):
        request.app.state.case_store = InMemoryCaseStore()
    return request.app.state.case_store


def get_investigation_registry(request: Request) -> InvestigationRegistry:
    if not hasattr(request.app.state, "investigation_registry"):
        request.app.state.investigation_registry = InvestigationRegistry()
    return request.app.state.investigation_registry


def get_tigergraph_client(request: Request, settings: Settings = Depends(get_settings_dependency)) -> TigerGraphClient:
    if not hasattr(request.app.state, "tigergraph_client"):
        request.app.state.tigergraph_client = get_client(settings)
    return request.app.state.tigergraph_client


def get_llm_client(settings: Settings = Depends(get_settings_dependency)) -> LLMClient:
    """The real provider only - never silently substitutes
    `FakeLLMClient` in a running API. Tests override this dependency
    directly (`app.dependency_overrides[get_llm_client] = lambda:
    FakeLLMClient(...)`); production without an LLM key configured fails
    the request with `DEPENDENCY_UNAVAILABLE`, not a fake decision."""
    try:
        return OpenAIChatClient(settings)
    except LLMNotConfiguredError as exc:
        raise DependencyUnavailableError(
            "The LLM is not configured for this deployment (OPENAI_API_KEY/OPENAI_MODEL unset).",
            details={"dependency": "llm"},
        ) from exc


def get_case_manager(case_store: CaseStore = Depends(get_case_store)) -> CaseManager:
    return CaseManager(case_store)


def get_case_memory(case_store: CaseStore = Depends(get_case_store)) -> CaseMemory:
    return CaseMemory(case_store)


def get_investigation_service() -> InvestigationService:
    """Stateless and cheap to construct (just wraps the fixed tool
    registry) - built fresh per request rather than cached, matching
    `AgentOrchestrator`'s own default in orchestrator.py."""
    return InvestigationService(build_default_registry())


def get_orchestrator(
    client: TigerGraphClient = Depends(get_tigergraph_client),
    case_store: CaseStore = Depends(get_case_store),
    llm_client: LLMClient = Depends(get_llm_client),
    investigation_service: InvestigationService = Depends(get_investigation_service),
) -> AgentOrchestrator:
    """Built fresh per request - `AgentOrchestrator` itself holds no
    request-specific mutable state beyond the graph it compiles once at
    construction (cheap, in-process, no I/O), and building it per
    request is what makes every sub-dependency above independently
    overridable in tests without a shared-orchestrator fixture."""
    return AgentOrchestrator(client, case_store, llm_client, investigation_service=investigation_service)


__all__ = [
    "get_case_manager",
    "get_case_memory",
    "get_case_store",
    "get_investigation_registry",
    "get_investigation_service",
    "get_llm_client",
    "get_orchestrator",
    "get_settings_dependency",
    "get_tigergraph_client",
]
