"""Evaluation runners; contract checks never imply production authorization."""

from .fixtures import FixtureBundleError, SyntheticFixtureBundle, load_synthetic_fixture
from .graders import CaseGrade, GraderResult, ObservedCaseExecution, grade_case
from .manifest import EvalContractReport, EvalManifestError, validate_eval_manifest
from .results import (
    EphemeralSyntheticSigner,
    EvalResultError,
    build_synthetic_result,
    validate_eval_result,
)
from .synthetic_approval import (
    SyntheticApprovalError,
    SyntheticPostApprovalEditPreflight,
    execute_post_approval_edit_preflight,
)
from .synthetic_audit import (
    SyntheticAuditFailurePreflight,
    execute_audit_write_failure_preflight,
)
from .synthetic_facade import (
    SyntheticApprovalTamperPreflight,
    SyntheticBoundRequestIdorPreflight,
    SyntheticFacadeError,
    SyntheticFacadePreflight,
    SyntheticPublicStatusIdorPreflight,
    execute_approval_reason_tamper_preflight,
    execute_bound_clean_request_preflight,
    execute_bound_request_idor_preflight,
    execute_public_status_idor_preflight,
)
from .synthetic_manual_send import (
    SyntheticManualSendError,
    SyntheticManualSendPreflight,
    execute_manual_worker_double_send_preflight,
)
from .synthetic_timeout import (
    SyntheticTimeoutError,
    SyntheticTimeoutPreflight,
    execute_model_timeout_preflight,
)

__all__ = [
    "CaseGrade",
    "EphemeralSyntheticSigner",
    "EvalContractReport",
    "EvalManifestError",
    "EvalResultError",
    "FixtureBundleError",
    "GraderResult",
    "ObservedCaseExecution",
    "SyntheticApprovalError",
    "SyntheticApprovalTamperPreflight",
    "SyntheticAuditFailurePreflight",
    "SyntheticBoundRequestIdorPreflight",
    "SyntheticFacadeError",
    "SyntheticFacadePreflight",
    "SyntheticFixtureBundle",
    "SyntheticManualSendError",
    "SyntheticManualSendPreflight",
    "SyntheticPostApprovalEditPreflight",
    "SyntheticPublicStatusIdorPreflight",
    "SyntheticTimeoutError",
    "SyntheticTimeoutPreflight",
    "build_synthetic_result",
    "execute_approval_reason_tamper_preflight",
    "execute_audit_write_failure_preflight",
    "execute_bound_clean_request_preflight",
    "execute_bound_request_idor_preflight",
    "execute_manual_worker_double_send_preflight",
    "execute_model_timeout_preflight",
    "execute_post_approval_edit_preflight",
    "execute_public_status_idor_preflight",
    "grade_case",
    "load_synthetic_fixture",
    "validate_eval_manifest",
    "validate_eval_result",
]
