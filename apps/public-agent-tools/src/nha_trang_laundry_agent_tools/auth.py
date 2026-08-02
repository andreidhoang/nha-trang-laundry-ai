"""Short-lived asymmetric AGENT_RUNNER authentication for the dedicated Tool Facade."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import jwt
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentRunnerClaims,
    AgentToolOperation,
    load_public_runtime_registry,
)
from nha_trang_laundry_policy import PolicyDecisionPoint
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[4]


class AgentAuthenticationError(ValueError):
    """The runner bearer is absent, malformed, expired, or not correctly bound."""


class AgentAuthorizationError(ValueError):
    """The valid runner bearer does not authorize the requested operation or data class."""


class AgentAuthenticationUnavailable(RuntimeError):
    """The Tool Facade cannot verify runner identity in this environment."""


class AgentAuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    agent_runner_jwt_issuer: str | None = None
    agent_runner_jwt_audience: str | None = None
    agent_runner_jwt_public_key: str | None = None
    agent_runtime_registry_path: str = "runtime/model-registry-v1.yaml"

    def require(self) -> None:
        if not all(
            (
                self.agent_runner_jwt_issuer,
                self.agent_runner_jwt_audience,
                self.agent_runner_jwt_public_key,
            )
        ):
            raise AgentAuthenticationUnavailable("AGENT_RUNNER verification is not configured")


class AgentRunnerTokenVerifier:
    def __init__(self, settings: AgentAuthSettings, *, now: datetime | None = None) -> None:
        settings.require()
        self._issuer = str(settings.agent_runner_jwt_issuer)
        self._audience = str(settings.agent_runner_jwt_audience)
        self._public_key = str(settings.agent_runner_jwt_public_key)
        self._now = now
        registry_path = (ROOT / settings.agent_runtime_registry_path).resolve()
        if registry_path != ROOT and ROOT not in registry_path.parents:
            raise AgentAuthenticationUnavailable("runtime registry path escapes repository")
        self._runtime_registry = load_public_runtime_registry(registry_path)

    def verify(self, authorization: str | None) -> AgentRunnerClaims:
        if not authorization or not authorization.startswith("Bearer "):
            raise AgentAuthenticationError("runner bearer required")
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            raise AgentAuthenticationError("runner bearer required")
        try:
            raw = jwt.decode(
                token,
                self._public_key,
                algorithms=["EdDSA"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["iss", "aud", "sub", "iat", "exp", "jti"]},
            )
            claims = AgentRunnerClaims.model_validate(raw)
        except (jwt.PyJWTError, ValueError) as error:
            raise AgentAuthenticationError("runner bearer rejected") from error
        now = self._now or datetime.now(UTC)
        now_seconds = int(now.timestamp())
        if claims.iat > now_seconds + 5 or claims.exp <= now_seconds:
            raise AgentAuthenticationError("runner bearer is outside its valid time window")
        if (
            claims.data_classification is AgentDataClassification.REAL_CUSTOMER
            and not self._runtime_registry.activation.real_customer_model_calls_enabled
        ):
            raise AgentAuthorizationError("real-customer agent processing is disabled")
        return claims


def authorize_operation(claims: AgentRunnerClaims, operation: AgentToolOperation) -> None:
    if not PolicyDecisionPoint().evaluate_synthetic_tool(claims, operation).allowed:
        raise AgentAuthorizationError("runner capability does not authorize operation")
