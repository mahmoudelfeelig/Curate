from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from feed_passport.domain import ActionType
from feed_passport.ports.live_platform import ValidatedLiveCertification

from .crypto import assert_no_stored_credentials


CERTIFICATION_FORMAT = "feed-passport-live-certification/v1"
_FIELDS = frozenset(
    {
        "format",
        "platform",
        "environment",
        "account_class",
        "result",
        "certified_at",
        "expires_at",
        "code_revision",
        "execute",
        "observe",
        "verify",
        "rollback",
        "receipt_ref",
        "provider_approval_ref",
        "checks",
        "evidence_sha256",
        "signature",
    }
)


class LiveCertificationError(ValueError):
    pass


class LiveCertificationVerifier:
    """Verify signed conformance receipts for promotion or read-only recovery."""

    def __init__(
        self,
        *,
        hmac_key: bytes,
        expected_revision: str,
        now: datetime | None = None,
    ) -> None:
        if len(hmac_key) < 32:
            raise ValueError("live certification verification key must contain at least 32 bytes")
        revision = expected_revision.strip()
        if len(revision) != 40 or any(
            character not in "0123456789abcdef" for character in revision
        ):
            raise ValueError("expected live certification revision must be exact lowercase Git SHA")
        self._key = bytes(hmac_key)
        self._expected_revision = revision
        self._now = now

    def verify(self, value: Mapping[str, Any]) -> ValidatedLiveCertification:
        """Verify a currently active receipt that may promote mutation capability."""

        return self._verify(value, allow_expired_for_reconciliation=False)

    def verify_for_reconciliation(
        self,
        value: Mapping[str, Any],
    ) -> ValidatedLiveCertification:
        """Verify an active or expired receipt for observation-only recovery.

        This deliberately accepts historical expiry, but retains every other
        signature, exact-revision, environment, action-subset, and start-time
        check. Adapters still independently reject mutation after expiry.
        """

        return self._verify(value, allow_expired_for_reconciliation=True)

    def _verify(
        self,
        value: Mapping[str, Any],
        *,
        allow_expired_for_reconciliation: bool,
    ) -> ValidatedLiveCertification:
        if set(value) != _FIELDS:
            missing = sorted(_FIELDS - set(value))
            extra = sorted(set(value) - _FIELDS)
            raise LiveCertificationError(
                f"live certification fields do not match the contract; missing={missing}, extra={extra}"
            )
        assert_no_stored_credentials(value, path="live_certification")
        if value["format"] != CERTIFICATION_FORMAT:
            raise LiveCertificationError("unsupported live certification format")
        if value["environment"] != "authorized_live" or value["account_class"] != "dummy":
            raise LiveCertificationError("certification must come from an authorized dummy account")
        if value["result"] != "passed":
            raise LiveCertificationError("only a passed conformance receipt can promote capabilities")
        platform = _required_string(value, "platform")
        receipt_ref = _required_string(value, "receipt_ref")
        revision = _required_string(value, "code_revision")
        if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
            raise LiveCertificationError("certification requires the exact lowercase Git revision")
        if not hmac.compare_digest(revision, self._expected_revision):
            raise LiveCertificationError("certification belongs to a different deployed Git revision")
        if platform == "reddit" and not _required_string(value, "provider_approval_ref"):
            raise LiveCertificationError("Reddit promotion requires an explicit provider approval reference")
        checks = value["checks"]
        if not isinstance(checks, list) or not checks or not all(
            isinstance(check, dict)
            and check.get("result") == "passed"
            and isinstance(check.get("name"), str)
            and check["name"].strip()
            for check in checks
        ):
            raise LiveCertificationError("every conformance check must be named and passed")
        certified_at = _parse_time(value["certified_at"], "certified_at")
        expires_at = _parse_time(value["expires_at"], "expires_at")
        now = self._now or datetime.now(timezone.utc)
        if certified_at > now or expires_at <= certified_at:
            raise LiveCertificationError(
                "live certification is not yet active, expired, or has an invalid lifetime"
            )
        if not allow_expired_for_reconciliation and expires_at <= now:
            raise LiveCertificationError(
                "live certification is not yet active, expired, or has an invalid lifetime"
            )
        execute = _actions(value["execute"], "execute")
        rollback = _actions(value["rollback"], "rollback", allow_empty=True)
        observe = _strings(value["observe"], "observe", allow_empty=True)
        verify = _strings(value["verify"], "verify")
        payload = {key: value[key] for key in sorted(_FIELDS - {"evidence_sha256", "signature"})}
        canonical = _canonical(payload)
        expected_digest = hashlib.sha256(canonical).hexdigest()
        if not hmac.compare_digest(str(value["evidence_sha256"]), expected_digest):
            raise LiveCertificationError("live certification evidence digest does not match")
        try:
            signature = base64.urlsafe_b64decode(
                str(value["signature"]) + "=" * (-len(str(value["signature"])) % 4)
            )
        except (ValueError, TypeError) as exc:
            raise LiveCertificationError("live certification signature is invalid") from exc
        expected_signature = hmac.new(self._key, canonical, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise LiveCertificationError("live certification signature does not match")
        return ValidatedLiveCertification(
            platform=platform,
            certified_at=certified_at,
            expires_at=expires_at,
            code_revision=revision,
            execute=execute,
            observe=observe,
            verify=verify,
            rollback=rollback,
            receipt_ref=receipt_ref,
            evidence_sha256=expected_digest,
        )

    def sign(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Complete an unsigned conformance payload; intended for the gated CLI."""

        if "evidence_sha256" in payload or "signature" in payload:
            raise LiveCertificationError("unsigned certification payload must not contain proof fields")
        if set(payload) != _FIELDS - {"evidence_sha256", "signature"}:
            raise LiveCertificationError("unsigned certification payload fields do not match the contract")
        assert_no_stored_credentials(payload, path="live_certification")
        canonical = _canonical({key: payload[key] for key in sorted(payload)})
        digest = hashlib.sha256(canonical).hexdigest()
        signature = base64.urlsafe_b64encode(
            hmac.new(self._key, canonical, hashlib.sha256).digest()
        ).rstrip(b"=").decode("ascii")
        value = {**dict(payload), "evidence_sha256": digest, "signature": signature}
        self.verify(value)
        return value

    def load_directory(self, directory: str | Path) -> dict[str, ValidatedLiveCertification]:
        """Load only currently active certifications for capability promotion."""

        return self._load_directory(directory, allow_expired_for_reconciliation=False)

    def load_directory_for_reconciliation(
        self,
        directory: str | Path,
    ) -> dict[str, ValidatedLiveCertification]:
        """Load signed active or historical receipts for restart recovery."""

        return self._load_directory(directory, allow_expired_for_reconciliation=True)

    def _load_directory(
        self,
        directory: str | Path,
        *,
        allow_expired_for_reconciliation: bool,
    ) -> dict[str, ValidatedLiveCertification]:
        root = Path(directory).resolve()
        if not root.is_dir():
            raise LiveCertificationError("live certification directory does not exist")
        values: dict[str, ValidatedLiveCertification] = {}
        for path in sorted(root.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise LiveCertificationError(f"invalid certification file: {path.name}") from exc
            if not isinstance(raw, dict):
                raise LiveCertificationError(f"certification file must contain an object: {path.name}")
            certification = (
                self.verify_for_reconciliation(raw)
                if allow_expired_for_reconciliation
                else self.verify(raw)
            )
            if certification.platform in values:
                raise LiveCertificationError(
                    f"more than one certification exists for {certification.platform}"
                )
            values[certification.platform] = certification
        return values


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _required_string(value: Mapping[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        if field == "provider_approval_ref" and item == "":
            return ""
        raise LiveCertificationError(f"{field} must be a non-empty string")
    return item.strip()


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise LiveCertificationError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LiveCertificationError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LiveCertificationError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _strings(value: Any, field: str, *, allow_empty: bool = False) -> frozenset[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise LiveCertificationError(f"{field} must be a non-empty string list")
    values = frozenset(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(values) != len(value):
        raise LiveCertificationError(f"{field} must contain unique non-empty strings")
    return values


def _actions(value: Any, field: str, *, allow_empty: bool = False) -> frozenset[ActionType]:
    values = _strings(value, field, allow_empty=allow_empty)
    try:
        return frozenset(ActionType(item) for item in values)
    except ValueError as exc:
        raise LiveCertificationError(f"{field} contains an unknown action type") from exc
