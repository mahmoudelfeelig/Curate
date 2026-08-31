from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
from re import fullmatch, sub
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from feed_passport.domain.models import CapabilityLevel, PlatformCapabilityManifest


CAPABILITY_CONTRACT_SCHEMA_VERSION = "1.0.0"
CAPABILITY_ADAPTER_VERSION = "1.0.0"
CAPABILITY_CONFORMANCE_SUITE_VERSION = "1.0.0"
CAPABILITY_CONFORMANCE_KEY_ID = "local-conformance-v1"
CAPABILITY_CONFORMANCE_REQUIRED_CHECK_IDS = frozenset(
    {
        "account_scope_isolated",
        "manifest_operations_verified",
        "action_policy_enforced",
        "public_engagement_forbidden",
        "rollback_contract_verified",
        "evidence_artifact_verified",
    }
)
CAPABILITY_CONTRACT_PUBLISHED_AT = "2026-08-31T00:00:00Z"
_PUBLIC_ENGAGEMENT_ACTIONS = frozenset({"like", "comment", "post", "repost", "send_message"})
_NON_SECRET_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@-]+$"
_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_CONFORMANCE_RECEIPT_FIELDS = frozenset(
    {
        "receipt_ref",
        "platform",
        "suite_version",
        "environment",
        "result",
        "run_at",
        "evidence_sha256",
        "key_id",
        "signature",
    }
)
_CONFORMANCE_EVIDENCE_FIELDS = frozenset(
    {"platform", "suite_version", "environment", "result", "run_at", "checks"}
)
_CONFORMANCE_CHECK_FIELDS = frozenset({"check_id", "passed", "evidence_ref"})


@dataclass(frozen=True, slots=True)
class ConformanceTrustStore:
    """Runtime-owned verifier keys, kept separate from submitted receipt material."""

    _trusted_keys: Mapping[str, bytes]

    @classmethod
    def from_keys(cls, trusted_keys: Mapping[str, str | bytes]) -> ConformanceTrustStore:
        normalized: dict[str, bytes] = {}
        for key_id, secret in trusted_keys.items():
            raw_secret = secret.encode("utf-8") if isinstance(secret, str) else secret
            if not key_id.strip() or not raw_secret:
                raise ValueError("conformance trust keys require a key ID and secret")
            normalized[str(key_id)] = bytes(raw_secret)
        if not normalized:
            raise ValueError("at least one conformance verifier key is required")
        return cls(MappingProxyType(normalized))

    def validate(
        self,
        receipt: Mapping[str, Any],
        *,
        evidence: Mapping[str, Any],
        platform: str,
        environment: str,
    ) -> dict[str, str]:
        normalized = _validate_conformance_receipt_shape(receipt)
        secret = self._trusted_keys.get(normalized["key_id"])
        if secret is None:
            raise ValueError("conformance receipt key is not trusted by this runtime")
        return _validate_conformance_receipt(
            receipt,
            evidence=evidence,
            secret=secret,
            platform=platform,
            environment=environment,
        )


def issue_conformance_receipt(
    *,
    receipt_ref: str,
    evidence: Mapping[str, Any],
    secret: str | bytes,
    key_id: str = CAPABILITY_CONFORMANCE_KEY_ID,
) -> dict[str, str]:
    """Issue authenticated receipt content for a separately executed conformance run."""

    normalized_evidence = _validate_conformance_evidence(evidence)
    payload = {
        "receipt_ref": receipt_ref,
        "platform": normalized_evidence["platform"],
        "suite_version": normalized_evidence["suite_version"],
        "environment": normalized_evidence["environment"],
        "result": normalized_evidence["result"],
        "run_at": normalized_evidence["run_at"],
        "evidence_sha256": hashlib.sha256(
            _canonical_conformance_evidence(normalized_evidence)
        ).hexdigest(),
        "key_id": key_id,
    }
    _validate_conformance_receipt_shape({**payload, "signature": "A" * 43})
    payload["signature"] = _receipt_signature(payload, secret)
    return payload


def _validate_conformance_receipt(
    receipt: Mapping[str, Any],
    *,
    evidence: Mapping[str, Any] | None,
    secret: str | bytes | None,
    platform: str,
    environment: str,
) -> dict[str, str]:
    if secret is None:
        raise ValueError("conformance receipt validation requires an authentication secret")
    if evidence is None:
        raise ValueError("conformance receipt validation requires canonical run evidence")
    normalized_evidence = _validate_conformance_evidence(evidence)
    normalized = _validate_conformance_receipt_shape(receipt)
    supplied_signature = normalized.pop("signature")
    expected_signature = _receipt_signature(normalized, secret)
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ValueError("conformance receipt signature is invalid")
    normalized["signature"] = supplied_signature
    evidence_sha256 = hashlib.sha256(
        _canonical_conformance_evidence(normalized_evidence)
    ).hexdigest()
    if not hmac.compare_digest(normalized["evidence_sha256"], evidence_sha256):
        raise ValueError("conformance receipt evidence hash does not match the supplied run evidence")
    for field_name in ("platform", "suite_version", "environment", "result", "run_at"):
        if normalized[field_name] != normalized_evidence[field_name]:
            raise ValueError(f"conformance receipt {field_name} does not match the run evidence")
    if normalized["platform"] != platform:
        raise ValueError("conformance receipt platform does not match the capability manifest")
    if normalized["suite_version"] != CAPABILITY_CONFORMANCE_SUITE_VERSION:
        raise ValueError("conformance receipt suite does not match the current conformance suite")
    if normalized["environment"] != environment:
        raise ValueError("conformance receipt environment does not match the capability level")
    if normalized["key_id"] != CAPABILITY_CONFORMANCE_KEY_ID:
        raise ValueError("conformance receipt key is not trusted by this runtime")
    return normalized


def _validate_conformance_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, Mapping) or set(evidence) != _CONFORMANCE_EVIDENCE_FIELDS:
        raise ValueError("conformance run evidence has invalid fields")
    platform = str(evidence["platform"])
    suite_version = str(evidence["suite_version"])
    environment = str(evidence["environment"])
    result = str(evidence["result"])
    run_at = str(evidence["run_at"])
    if not 1 <= len(platform) <= 80 or fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$", platform) is None:
        raise ValueError("conformance run evidence platform is invalid")
    if fullmatch(r"^[0-9]+\.[0-9]+\.[0-9]+$", suite_version) is None:
        raise ValueError("conformance run evidence suite_version is invalid")
    if environment not in {"authorized_live", "lab", "guided"}:
        raise ValueError("conformance run evidence environment is invalid")
    if result not in {"passed", "failed"}:
        raise ValueError("conformance run evidence result is invalid")
    parsed_run_at = datetime.fromisoformat(run_at)
    if parsed_run_at.tzinfo is None or parsed_run_at.utcoffset() is None:
        raise ValueError("conformance run evidence run_at must include a timezone")
    checks_value = evidence["checks"]
    if not isinstance(checks_value, (list, tuple)) or not checks_value:
        raise ValueError("conformance run evidence requires at least one check")
    checks: list[dict[str, Any]] = []
    for item in checks_value:
        if not isinstance(item, Mapping) or set(item) != _CONFORMANCE_CHECK_FIELDS:
            raise ValueError("conformance run evidence check fields are invalid")
        check_id = str(item["check_id"])
        evidence_ref = str(item["evidence_ref"])
        if not 2 <= len(check_id) <= 160 or fullmatch(_NON_SECRET_REF_PATTERN, check_id) is None:
            raise ValueError("conformance run evidence check_id is invalid")
        if not 2 <= len(evidence_ref) <= 160 or fullmatch(_NON_SECRET_REF_PATTERN, evidence_ref) is None:
            raise ValueError("conformance run evidence evidence_ref is invalid")
        if not isinstance(item["passed"], bool):
            raise ValueError("conformance run evidence passed must be boolean")
        checks.append(
            {
                "check_id": check_id,
                "passed": item["passed"],
                "evidence_ref": evidence_ref,
            }
        )
    check_ids = [item["check_id"] for item in checks]
    if len(set(check_ids)) != len(check_ids):
        raise ValueError("conformance run evidence check IDs must be unique")
    supplied_check_ids = frozenset(check_ids)
    if supplied_check_ids != CAPABILITY_CONFORMANCE_REQUIRED_CHECK_IDS:
        missing = sorted(CAPABILITY_CONFORMANCE_REQUIRED_CHECK_IDS - supplied_check_ids)
        unexpected = sorted(supplied_check_ids - CAPABILITY_CONFORMANCE_REQUIRED_CHECK_IDS)
        raise ValueError(
            "conformance run evidence does not cover the complete suite"
            + (f"; missing: {', '.join(missing)}" if missing else "")
            + (f"; unexpected: {', '.join(unexpected)}" if unexpected else "")
        )
    derived_result = "passed" if all(item["passed"] for item in checks) else "failed"
    if result != derived_result:
        raise ValueError("conformance run evidence result does not match its checks")
    return {
        "platform": platform,
        "suite_version": suite_version,
        "environment": environment,
        "result": result,
        "run_at": parsed_run_at.isoformat(),
        "checks": checks,
    }


def _canonical_conformance_evidence(evidence: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(evidence),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _validate_conformance_receipt_shape(receipt: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(receipt, Mapping):
        raise ValueError("conformance receipt must be structured content")
    fields = set(receipt)
    if fields != _CONFORMANCE_RECEIPT_FIELDS:
        missing = sorted(_CONFORMANCE_RECEIPT_FIELDS - fields)
        unexpected = sorted(fields - _CONFORMANCE_RECEIPT_FIELDS)
        raise ValueError(
            "conformance receipt fields are invalid"
            + (f"; missing: {', '.join(missing)}" if missing else "")
            + (f"; unexpected: {', '.join(unexpected)}" if unexpected else "")
        )
    normalized = {key: str(receipt[key]) for key in _CONFORMANCE_RECEIPT_FIELDS}
    for field_name in ("receipt_ref", "key_id"):
        value = normalized[field_name]
        if not 2 <= len(value) <= 160 or fullmatch(_NON_SECRET_REF_PATTERN, value) is None:
            raise ValueError(f"conformance receipt {field_name} is invalid")
    platform = normalized["platform"]
    if not 1 <= len(platform) <= 80 or fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$", platform) is None:
        raise ValueError("conformance receipt platform is invalid")
    if fullmatch(r"^[0-9]+\.[0-9]+\.[0-9]+$", normalized["suite_version"]) is None:
        raise ValueError("conformance receipt suite_version is invalid")
    if normalized["environment"] not in {"authorized_live", "lab", "guided"}:
        raise ValueError("conformance receipt environment is invalid")
    if normalized["result"] not in {"passed", "failed"}:
        raise ValueError("conformance receipt result is invalid")
    parsed_run_at = datetime.fromisoformat(normalized["run_at"])
    if parsed_run_at.tzinfo is None or parsed_run_at.utcoffset() is None:
        raise ValueError("conformance receipt run_at must include a timezone")
    normalized["run_at"] = parsed_run_at.isoformat()
    if fullmatch(_SHA256_PATTERN, normalized["evidence_sha256"]) is None:
        raise ValueError("conformance receipt evidence_sha256 is invalid")
    if fullmatch(r"^[A-Za-z0-9_-]{43}$", normalized["signature"]) is None:
        raise ValueError("conformance receipt signature is invalid")
    return normalized


def _receipt_signature(payload: Mapping[str, str], secret: str | bytes) -> str:
    raw_secret = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not raw_secret:
        raise ValueError("conformance receipt authentication secret is required")
    key = hashlib.sha256(b"feed-passport-conformance-v1\0" + raw_secret).digest()
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    signature = hmac.new(key, encoded, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


def _capability_slug(platform: str) -> str:
    return sub(r"[^a-z0-9]+", "_", platform.lower()).strip("_")


def _capability_operation_modes(manifest: PlatformCapabilityManifest) -> dict[str, str]:
    if manifest.level is CapabilityLevel.LAB:
        return {
            "observe": "lab",
            "execute": "lab",
            "sample": "lab",
            "rollback": "lab",
            "health": "lab",
        }
    if manifest.level is CapabilityLevel.GUIDED:
        return {
            "observe": "guided" if manifest.observe else "unavailable",
            "execute": "guided",
            "sample": "guided" if manifest.observe else "unavailable",
            "rollback": "guided" if manifest.rollback else "unavailable",
            "health": "guided",
        }
    if manifest.level is CapabilityLevel.UNAVAILABLE:
        return {
            "observe": "unavailable",
            "execute": "unavailable",
            "sample": "unavailable",
            "rollback": "unavailable",
            "health": "unavailable",
        }
    if manifest.certified_at is None:
        raise ValueError(
            "executable capability contracts require an authorized live certification timestamp"
        )
    return {
        "observe": "authorized" if manifest.observe else "unavailable",
        "execute": "authorized",
        "sample": "authorized" if manifest.observe else "unavailable",
        "rollback": "authorized" if manifest.rollback else "unavailable",
        "health": "authorized",
    }


def capability_manifest_to_contract(
    manifest: PlatformCapabilityManifest,
    *,
    adapter_name: str,
    evidence_urls: Iterable[str] = (),
    conformance_receipt: Mapping[str, Any] | None = None,
    conformance_evidence: Mapping[str, Any] | None = None,
    conformance_trust: ConformanceTrustStore | None = None,
) -> dict[str, Any]:
    """Serialize the runtime manifest into the strict public JSON contract.

    The dataclass remains the policy authority. This function only normalizes it
    into the versioned contract used by the existing platform listing.
    """

    if not adapter_name.strip():
        raise ValueError("adapter_name is required")
    if not manifest.evidence_url:
        raise ValueError("a capability contract requires an evidence URL")
    contract_evidence_urls = list(
        dict.fromkeys((manifest.evidence_url, *(str(value) for value in evidence_urls)))
    )
    allowed_action_kinds = sorted(
        action.value for action in manifest.execute | manifest.requires_user_handoff
    )
    forbidden = _PUBLIC_ENGAGEMENT_ACTIONS & set(allowed_action_kinds)
    if forbidden:
        raise ValueError(
            "capability contracts cannot expose public engagement actions: "
            + ", ".join(sorted(forbidden))
        )
    operation_modes = _capability_operation_modes(manifest)

    platform_slug = _capability_slug(manifest.platform)
    run_at = (
        manifest.certified_at.isoformat()
        if manifest.certified_at is not None
        else CAPABILITY_CONTRACT_PUBLISHED_AT
    )
    expected_environment = (
        "lab"
        if manifest.level is CapabilityLevel.LAB
        else "guided"
        if manifest.level is CapabilityLevel.GUIDED
        else "authorized_live"
    )
    if (conformance_receipt is None) != (conformance_evidence is None):
        raise ValueError("conformance receipt and run evidence must be supplied together")
    if conformance_receipt is not None and conformance_trust is None:
        raise ValueError("conformance receipt validation requires a runtime trust store")
    validated_receipt = (
        conformance_trust.validate(
            conformance_receipt,
            evidence=conformance_evidence,
            platform=manifest.platform,
            environment=expected_environment,
        )
        if conformance_receipt is not None and conformance_trust is not None
        else None
    )
    if manifest.level is CapabilityLevel.UNAVAILABLE and validated_receipt is not None:
        raise ValueError("unavailable capability contracts cannot carry conformance receipts")
    if manifest.level in {CapabilityLevel.CLOSED_LOOP, CapabilityLevel.EXECUTABLE}:
        if validated_receipt is None:
            raise ValueError(
                "executable capability contracts require a validated conformance receipt"
            )
        if validated_receipt["result"] != "passed":
            raise ValueError("executable capability contracts require a passed conformance receipt")
        if manifest.certified_at is None or validated_receipt["run_at"] != manifest.certified_at.isoformat():
            raise ValueError(
                "executable capability conformance time does not match its certification record"
            )

    if manifest.level is CapabilityLevel.UNAVAILABLE or validated_receipt is None:
        conformance = {
            "suite_version": CAPABILITY_CONFORMANCE_SUITE_VERSION,
            "run_id": f"conformance.not_run.{platform_slug}.v1",
            "environment": "not_run",
            "result": "not_run",
            "run_at": run_at,
        }
    else:
        conformance = {
            "suite_version": validated_receipt["suite_version"],
            "run_id": validated_receipt["receipt_ref"],
            "environment": validated_receipt["environment"],
            "result": validated_receipt["result"],
            "receipt_ref": validated_receipt["receipt_ref"],
            "evidence_sha256": validated_receipt["evidence_sha256"],
            "receipt_key_id": validated_receipt["key_id"],
            "receipt_signature": validated_receipt["signature"],
            "run_at": validated_receipt["run_at"],
        }

    if manifest.level is CapabilityLevel.LAB and manifest.platform.startswith("twin:"):
        limitations = [
            "Explicitly synthetic deterministic local control twin; no real account is connected.",
            (
                "Fixture responses prove declared control semantics only and do not claim "
                "private ranking fidelity."
            ),
        ]
    elif manifest.level is CapabilityLevel.LAB:
        limitations = [
            "Explicitly synthetic deterministic proof environment; no live-platform integration is claimed.",
            "Fixture feed behavior is not evidence of any private platform ranking system.",
        ]
    elif manifest.level is CapabilityLevel.GUIDED:
        limitations = [
            (
                "Credential-free planning runtime only; actions require review in the "
                "platform's official interface."
            ),
            (
                "Any seeded demo account is an explicitly synthetic normalized snapshot, "
                "not captured account data."
            ),
            (
                "Synthetic snapshot content is not evidence of private ranking behavior or "
                "ranking fidelity."
            ),
        ]
    else:
        limitations = [
            "Only the operations and actions declared in this versioned manifest are certified.",
        ]
    if not manifest.rollback:
        limitations.append("No runtime rollback transport is certified for this adapter.")
    if not manifest.verify:
        limitations.append("No runtime verification surface is certified for this adapter.")

    return {
        "schema_version": CAPABILITY_CONTRACT_SCHEMA_VERSION,
        "manifest_id": f"cap_{platform_slug}_runtime_v1",
        "manifest_version": 1,
        "platform": manifest.platform,
        "adapter_name": adapter_name,
        "adapter_version": CAPABILITY_ADAPTER_VERSION,
        "evidence_level": manifest.level.value,
        "operations": operation_modes,
        "allowed_action_kinds": allowed_action_kinds,
        "evidence_urls": contract_evidence_urls,
        "limitations": limitations,
        "trust_boundary": {
            "credentials_in_model_context": False,
            "public_engagement_automation": False,
            "raw_private_history_transfer": False,
        },
        "conformance": conformance,
        "published_at": (
            manifest.certified_at.isoformat()
            if manifest.certified_at is not None
            else CAPABILITY_CONTRACT_PUBLISHED_AT
        ),
    }


def to_primitive(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: to_primitive(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset, tuple, list)):
        return [to_primitive(item) for item in value]
    if isinstance(value, (dict, Mapping, MappingProxyType)):
        return {
            str(key.value if isinstance(key, Enum) else key): to_primitive(item)
            for key, item in value.items()
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"cannot serialize {type(value).__name__}")


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("serialized datetime must include a timezone")
    return parsed
