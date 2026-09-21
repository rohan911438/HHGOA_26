"""Application configuration.

All settings come from environment variables (loaded from .env in
development). Nothing here carries a real credential default — every
secret field defaults to empty so a missing value fails loudly instead
of silently connecting somewhere unintended.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def is_placeholder(value: str) -> bool:
    """True for an unedited .env.example value.

    Copying .env.example to .env leaves values like YOUR_TIGERGRAPH_HOST in
    place. Treating those as configured produces a confusing DNS error
    instead of "you have not filled this in yet", so they count as unset.
    """
    if not value:
        return True
    stripped = value.strip()
    return stripped.startswith("YOUR_") or stripped in {
        "https://YOUR_TIGERGRAPH_HOST",
        "changeme",
        "<unset>",
    }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- Application ----------------
    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "hhgoa-fraud-agent"
    app_version: str = "0.1.0"
    log_level: str = "INFO"

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ---------------- TigerGraph ----------------
    tg_host: str = ""
    tg_graphname: str = ""
    tg_username: str = ""
    tg_password: SecretStr = SecretStr("")
    tg_secret: SecretStr = SecretStr("")
    tg_api_token: SecretStr = SecretStr("")
    tg_jwt_token: SecretStr = SecretStr("")
    tg_restpp_port: int = 443
    tg_gs_port: int = 14240
    tg_ssl_port: int = 443
    tg_tgcloud: bool = True
    tg_cert_path: str = ""
    tg_read_only: bool = False
    tg_timeout_seconds: int = 60

    # ---------------- TigerGraph MCP ----------------
    tg_mcp_profile: str = "default"
    tg_mcp_transport: Literal["stdio", "http"] = "stdio"
    tg_mcp_host: str = "127.0.0.1"
    tg_mcp_port: int = 8001

    # ---------------- LLM ----------------
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = ""
    openai_base_url: str = ""
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2

    # ---------------- Embeddings ----------------
    embedding_provider: Literal["openai", "local", "none"] = "openai"
    embedding_model: str = ""

    # ---------------- Persistence ----------------
    database_url: str = "sqlite:///./data/fraud_agent.db"
    vector_store: Literal["local"] = "local"
    vector_db_path: str = "./data/vector_store"

    # ---------------- Dataset ----------------
    dataset_root: str = "./data/hhgoa"
    transactions_path: str = "./data/hhgoa/transactions"
    cases_path: str = "./data/hhgoa/cases"
    policies_path: str = "./data/hhgoa/policies"
    benchmark_path: str = "./data/hhgoa/benchmark"

    # ---------------- Mock switches ----------------
    mock_external_actions: bool = True
    mock_tigergraph: bool = False

    # ---------------- Derived helpers ----------------

    @field_validator("tg_host")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def tg_host_set(self) -> bool:
        return not is_placeholder(self.tg_host)

    @property
    def tg_graph_set(self) -> bool:
        return not is_placeholder(self.tg_graphname)

    @property
    def tg_configured(self) -> bool:
        """True when we have a real host, a real graph and one credential."""
        return self.tg_host_set and self.tg_graph_set and self.tg_auth_method != "none"

    @property
    def tg_auth_method(self) -> str:
        """Which credential the client will prefer. Token > JWT > secret > password."""
        if not is_placeholder(self.tg_api_token.get_secret_value()):
            return "api_token"
        if not is_placeholder(self.tg_jwt_token.get_secret_value()):
            return "jwt"
        if not is_placeholder(self.tg_secret.get_secret_value()):
            return "secret"
        if not is_placeholder(self.tg_password.get_secret_value()):
            return "password"
        return "none"

    @property
    def llm_configured(self) -> bool:
        return not is_placeholder(self.openai_api_key.get_secret_value()) and not is_placeholder(
            self.openai_model
        )

    def resolve(self, relative: str) -> Path:
        """Resolve a configured path relative to the backend root."""
        p = Path(relative)
        return p if p.is_absolute() else (BACKEND_ROOT / p).resolve()

    def redacted(self) -> dict:
        """Config snapshot safe to log or return from a health endpoint."""
        return {
            "app_env": self.app_env,
            "app_name": self.app_name,
            "app_version": self.app_version,
            "tg_host": self.tg_host or None,
            "tg_graphname": self.tg_graphname or None,
            "tg_username": self.tg_username or None,
            "tg_auth_method": self.tg_auth_method,
            "tg_tgcloud": self.tg_tgcloud,
            "tg_restpp_port": self.tg_restpp_port,
            "tg_gs_port": self.tg_gs_port,
            "tg_configured": self.tg_configured,
            "tg_read_only": self.tg_read_only,
            "mcp_transport": self.tg_mcp_transport,
            "llm_model": self.openai_model or None,
            "llm_configured": self.llm_configured,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model or None,
            "mock_external_actions": self.mock_external_actions,
            "mock_tigergraph": self.mock_tigergraph,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
