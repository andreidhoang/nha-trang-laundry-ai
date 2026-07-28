from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    AgentRunnerClaims,
    ReleaseCapability,
)


def claims(**overrides: object) -> AgentRunnerClaims:
    now = int(datetime.now(UTC).timestamp())
    values: dict[str, object] = {
        "iss": "https://control-plane.test",
        "aud": "agent-tool-facade",
        "sub": "AGENT_RUNNER",
        "iat": now,
        "exp": now + 30,
        "jti": uuid4(),
        "run_id": uuid4(),
        "organization_id": uuid4(),
        "store_id": uuid4(),
        "channel": "INTERNAL_TEST",
        "conversation_binding_id": uuid4(),
        "contact_binding_id": uuid4(),
        "capabilities": (ReleaseCapability.INTERNAL_SHADOW,),
        "stage": AgentDeploymentStage.SHADOW,
        "data_classification": AgentDataClassification.SYNTHETIC,
    }
    values.update(overrides)
    return AgentRunnerClaims.model_validate(values)


def test_runner_claims_allow_one_server_selected_capability() -> None:
    assert claims().capabilities == (ReleaseCapability.INTERNAL_SHADOW,)
    with pytest.raises(ValueError, match="at most 1 item"):
        claims(capabilities=(ReleaseCapability.PUBLIC_FAQ, ReleaseCapability.QUOTE_ESTIMATE))


def test_runner_claim_lifetime_is_strictly_bounded() -> None:
    now = int(datetime.now(UTC).timestamp())
    with pytest.raises(ValueError, match="between 1 and 60 seconds"):
        claims(iat=now, exp=now + 61)
