from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping
from uuid import uuid4

from feed_passport.application import CuratorApplication
from feed_passport.infrastructure.serialization import to_primitive


class ConsentError(PermissionError):
    """Raised when an approval grant is absent, stale, altered, or replayed."""


def require_model_alert_only_monitor(
    *,
    mode: str,
    allowed_actions: Collection[object],
) -> None:
    """Prevent a model-facing path from creating a future mutation mandate."""

    if mode != "alert_only" or allowed_actions:
        raise PermissionError(
            "Model-facing agents can schedule alert-only drift monitors only; "
            "a human must use the explicit local UI/API approval surface to authorize "
            "bounded automatic actions."
        )


@dataclass(frozen=True, slots=True)
class ConsentGrant:
    token: str
    operation: str
    resource_id: str
    actor_id: str
    max_total_actions: int | None
    issued_at: datetime
    expires_at: datetime
    summary: Mapping[str, Any]


class ConsentBroker:
    """Issues short-lived, resource-bound, one-time approval grants.

    The model can ask for consent, but only deterministic code can mint and consume
    a grant. The signed payload is bound to the current migration plan or receipt,
    so changing the proposed action set invalidates an older approval.
    """

    def __init__(
        self,
        application: CuratorApplication,
        *,
        secret: bytes | str | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.application = application
        raw_secret = secret.encode("utf-8") if isinstance(secret, str) else secret
        self._secret = hashlib.sha256(raw_secret or secrets.token_bytes(32)).digest()
        self._clock = clock or application.clock
        self._id_factory = id_factory or (lambda: uuid4().hex)

    def issue_for_migration(
        self,
        migration_id: str,
        *,
        actor_id: str,
        max_total_actions: int | None = None,
        ttl: timedelta = timedelta(minutes=10),
    ) -> ConsentGrant:
        migration = self.application.projection_get("migrations", migration_id)
        if migration["status"] != "awaiting_approval":
            raise ConsentError("only a previewed migration can be approved")
        passport = self.application.get_passport(str(migration["passport_id"]))
        if passport.owner_id != actor_id:
            raise ConsentError("only the Passport owner can approve this migration")
        action_count = len(migration["plan"]["actions"])
        if action_count < 1:
            raise ConsentError("this destination is already aligned; there are no actions to approve")
        budget = action_count if max_total_actions is None else int(max_total_actions)
        if budget < 1 or budget > action_count:
            raise ConsentError("approval budget must be between one and the proposed action count")
        fingerprint = self._fingerprint(migration["plan"])
        return self._issue(
            operation="execute_migration",
            resource_id=migration_id,
            actor_id=actor_id,
            max_total_actions=budget,
            fingerprint=fingerprint,
            ttl=ttl,
            summary={
                "platform": migration["platform"],
                "destination_account_id": migration["destination_account_id"],
                "action_count": action_count,
                "approved_action_count": budget,
                "loss_count": len(migration["plan"].get("losses", ())),
            },
        )

    def issue_for_rollback(
        self,
        receipt_id: str,
        *,
        actor_id: str,
        platform: str,
        ttl: timedelta = timedelta(minutes=10),
    ) -> ConsentGrant:
        receipt = self.application.projection_get("receipts", receipt_id)
        passport = self.application.get_passport(str(receipt["passport_id"]))
        if passport.owner_id != actor_id:
            raise ConsentError("only the Passport owner can approve this rollback")
        if receipt.get("status") == "rolled_back":
            raise ConsentError("this receipt has already been rolled back")
        receipt_platform = receipt.get("platform")
        if not isinstance(receipt_platform, str) or not receipt_platform:
            raise ConsentError("receipt is missing its bound platform")
        if platform != receipt_platform:
            raise ConsentError("rollback approval platform does not match the receipt")
        return self._issue(
            operation="rollback_receipt",
            resource_id=receipt_id,
            actor_id=actor_id,
            max_total_actions=None,
            fingerprint=self._fingerprint(receipt),
            ttl=ttl,
            summary={
                "platform": receipt_platform,
                "destination_account_id": receipt["destination_id"],
                "reversible_action_count": sum(
                    1
                    for outcome in receipt.get("outcomes", ())
                    if outcome.get("status") == "executed" and outcome.get("action", {}).get("reversible")
                ),
            },
        )

    def issue_for_mission(
        self,
        mission_id: str,
        *,
        actor_id: str,
        ttl: timedelta = timedelta(minutes=10),
    ) -> ConsentGrant:
        mission = self.application.projection_get("agent_missions", mission_id)
        if mission["status"] != "awaiting_approval":
            raise ConsentError("only a previewed mission awaiting approval can be approved")
        passport = self.application.get_passport(str(mission["passport_id"]))
        if passport.owner_id != actor_id or mission.get("owner_id") != actor_id:
            raise ConsentError("only the mission owner can approve it")
        action_count = int(mission.get("pending_plan", {}).get("action_count", 0))
        if action_count < 1:
            raise ConsentError("this mission has no actionable local-twin plan to approve")
        scope = mission.get("approval_scope")
        if not isinstance(scope, Mapping):
            raise ConsentError("mission approval scope is missing")
        total_budget = int(mission["budget"]["total_actions"])
        return self._issue(
            operation="execute_agent_mission",
            resource_id=mission_id,
            actor_id=actor_id,
            max_total_actions=total_budget,
            fingerprint=self._fingerprint(scope),
            ttl=ttl,
            summary={
                "destination_twin": mission["destination_twin"],
                "destination_account_id": mission["destination_account_id"],
                "total_action_budget": total_budget,
                "per_iteration_action_budget": mission["budget"]["per_iteration_actions"],
                "max_iterations": mission["budget"]["max_iterations"],
                "allowed_action_types": list(mission.get("allowed_action_types", ())),
            },
        )

    def issue_for_mission_rollback(
        self,
        mission_id: str,
        *,
        actor_id: str,
        ttl: timedelta = timedelta(minutes=10),
    ) -> ConsentGrant:
        mission = self.application.projection_get("agent_missions", mission_id)
        if mission.get("owner_id") != actor_id:
            raise ConsentError("only the mission owner can approve its rollback")
        if mission.get("status") == "rolled_back":
            raise ConsentError("this mission has already been rolled back")
        if mission.get("status") not in {
            "completed",
            "needs_human",
            "failed_recoverable",
            "rollback_partial",
        }:
            raise ConsentError("only a terminal executed mission can be approved for rollback")
        receipt_ids = [str(item) for item in mission.get("receipt_ids", ())]
        if not receipt_ids:
            raise ConsentError("this mission has no executed receipts to roll back")
        scope = self._mission_rollback_scope(mission_id)
        return self._issue(
            operation="rollback_agent_mission",
            resource_id=mission_id,
            actor_id=actor_id,
            max_total_actions=None,
            fingerprint=self._fingerprint(scope),
            ttl=ttl,
            summary={
                "destination_twin": mission["destination_twin"],
                "destination_account_id": mission["destination_account_id"],
                "receipt_count": len(receipt_ids),
                "rollback_order": list(reversed(receipt_ids)),
            },
        )

    def inspect(
        self,
        token: str,
        *,
        operation: str,
        resource_id: str,
        actor_id: str,
    ) -> Mapping[str, Any]:
        payload = self._decode(token)
        self._assert_scope(payload, operation=operation, resource_id=resource_id, actor_id=actor_id)
        expected_fingerprint = self._current_fingerprint(operation, resource_id)
        if not hmac.compare_digest(str(payload["fingerprint"]), expected_fingerprint):
            raise ConsentError("the approved resource changed; request a fresh preview and approval")
        return payload

    def consume(
        self,
        token: str,
        *,
        operation: str,
        resource_id: str,
        actor_id: str,
    ) -> Mapping[str, Any]:
        payload = self.inspect(
            token,
            operation=operation,
            resource_id=resource_id,
            actor_id=actor_id,
        )
        payload_hash = self._fingerprint(payload)
        first_use = self.application.store.reserve_idempotency(
            f"consent:{payload['jti']}",
            payload_hash,
            self._now(),
        )
        if not first_use:
            raise ConsentError("this one-time approval has already been used")
        return payload

    def _issue(
        self,
        *,
        operation: str,
        resource_id: str,
        actor_id: str,
        max_total_actions: int | None,
        fingerprint: str,
        ttl: timedelta,
        summary: Mapping[str, Any],
    ) -> ConsentGrant:
        if ttl <= timedelta(0) or ttl > timedelta(minutes=15):
            raise ValueError("consent lifetime must be between zero and fifteen minutes")
        issued_at = self._now()
        expires_at = issued_at + ttl
        payload = {
            "version": 1,
            "jti": self._id_factory(),
            "operation": operation,
            "resource_id": resource_id,
            "actor_id": actor_id,
            "max_total_actions": max_total_actions,
            "fingerprint": fingerprint,
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        encoded_payload = self._encode_json(payload)
        signature = self._encode_bytes(hmac.new(self._secret, encoded_payload.encode("ascii"), hashlib.sha256).digest())
        return ConsentGrant(
            token=f"{encoded_payload}.{signature}",
            operation=operation,
            resource_id=resource_id,
            actor_id=actor_id,
            max_total_actions=max_total_actions,
            issued_at=issued_at,
            expires_at=expires_at,
            summary=dict(summary),
        )

    def _decode(self, token: str) -> dict[str, Any]:
        try:
            encoded_payload, encoded_signature = token.split(".", 1)
            payload_bytes = self._decode_bytes(encoded_payload)
            supplied = self._decode_bytes(encoded_signature)
            if self._encode_bytes(payload_bytes) != encoded_payload or self._encode_bytes(supplied) != encoded_signature:
                raise ConsentError("approval token encoding is non-canonical")
            expected = hmac.new(self._secret, encoded_payload.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(supplied, expected):
                raise ConsentError("approval signature is invalid")
            payload = json.loads(payload_bytes)
        except ConsentError:
            raise
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ConsentError("approval token is malformed") from exc
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ConsentError("approval token version is unsupported")
        return payload

    def _assert_scope(
        self,
        payload: Mapping[str, Any],
        *,
        operation: str,
        resource_id: str,
        actor_id: str,
    ) -> None:
        if payload.get("operation") != operation:
            raise ConsentError("approval does not cover this operation")
        if payload.get("resource_id") != resource_id:
            raise ConsentError("approval does not cover this resource")
        if payload.get("actor_id") != actor_id:
            raise ConsentError("approval belongs to a different person")
        try:
            expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        except (KeyError, ValueError) as exc:
            raise ConsentError("approval expiry is invalid") from exc
        if self._now() >= expires_at:
            raise ConsentError("approval has expired")

    def _current_fingerprint(self, operation: str, resource_id: str) -> str:
        if operation == "execute_migration":
            resource = self.application.projection_get("migrations", resource_id)["plan"]
        elif operation == "rollback_receipt":
            resource = self.application.projection_get("receipts", resource_id)
        elif operation == "execute_agent_mission":
            mission = self.application.projection_get("agent_missions", resource_id)
            resource = mission.get("approval_scope")
            if not isinstance(resource, Mapping):
                raise ConsentError("mission approval scope is missing")
        elif operation == "rollback_agent_mission":
            resource = self._mission_rollback_scope(resource_id)
        else:
            raise ConsentError("approval operation is unsupported")
        return self._fingerprint(resource)

    def _mission_rollback_scope(self, mission_id: str) -> dict[str, Any]:
        mission = self.application.projection_get("agent_missions", mission_id)
        receipt_ids = [str(item) for item in mission.get("receipt_ids", ())]
        return {
            "mission_id": mission_id,
            "owner_id": mission["owner_id"],
            "destination_twin": mission["destination_twin"],
            "destination_account_id": mission["destination_account_id"],
            "receipts": [
                self.application.projection_get("receipts", receipt_id)
                for receipt_id in receipt_ids
            ],
        }

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("consent clock must be timezone-aware")
        return value

    @staticmethod
    def _fingerprint(value: Any) -> str:
        body = json.dumps(to_primitive(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(body).hexdigest()

    @staticmethod
    def _encode_json(value: Mapping[str, Any]) -> str:
        return ConsentBroker._encode_bytes(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )

    @staticmethod
    def _encode_bytes(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode_bytes(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)
