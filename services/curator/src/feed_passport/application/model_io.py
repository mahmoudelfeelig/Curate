from __future__ import annotations

from typing import Any, Mapping

from feed_passport.domain.models import (
    ActionOutcome,
    ActionReceipt,
    ActionStatus,
    ActionType,
    FeedPassport,
    PassportOverlay,
    OverlayMode,
    PreferenceEvidence,
    ProposedAction,
    ShareablePassportSlice,
)
from feed_passport.infrastructure.serialization import parse_datetime, to_primitive


def passport_to_dict(value: FeedPassport) -> dict[str, Any]:
    return to_primitive(value)


def passport_from_dict(value: Mapping[str, Any]) -> FeedPassport:
    return FeedPassport(
        id=str(value["id"]),
        owner_id=str(value["owner_id"]),
        name=str(value["name"]),
        version=int(value["version"]),
        intent=str(value["intent"]),
        topic_targets={str(key): float(item) for key, item in dict(value["topic_targets"]).items()},
        creator_preferences={
            str(key): float(item) for key, item in dict(value.get("creator_preferences", {})).items()
        },
        format_preferences={
            str(key): float(item) for key, item in dict(value.get("format_preferences", {})).items()
        },
        languages=tuple(str(item) for item in value.get("languages", ("en",))),
        hard_exclusions=frozenset(str(item) for item in value.get("hard_exclusions", ())),
        serendipity=float(value.get("serendipity", 0.2)),
        max_outrage=float(value.get("max_outrage", 0.05)),
        max_source_share=float(value.get("max_source_share", 0.25)),
        created_at=parse_datetime(str(value["created_at"])),
        updated_at=parse_datetime(str(value["updated_at"])),
        expires_at=parse_datetime(str(value["expires_at"])) if value.get("expires_at") else None,
        provenance=tuple(
            PreferenceEvidence(
                source=str(item["source"]),
                reference=str(item["reference"]),
                confidence=float(item["confidence"]),
                observed_at=parse_datetime(str(item["observed_at"])),
            )
            for item in value.get("provenance", ())
        ),
    )


def overlay_to_dict(value: PassportOverlay) -> dict[str, Any]:
    return to_primitive(value)


def overlay_from_dict(value: Mapping[str, Any]) -> PassportOverlay:
    return PassportOverlay(
        id=str(value["id"]),
        base_passport_id=str(value["base_passport_id"]),
        name=str(value["name"]),
        topic_adjustments={str(key): float(item) for key, item in dict(value["topic_adjustments"]).items()},
        add_exclusions=frozenset(str(item) for item in value.get("add_exclusions", ())),
        remove_exclusions=frozenset(str(item) for item in value.get("remove_exclusions", ())),
        starts_at=parse_datetime(str(value["starts_at"])),
        expires_at=parse_datetime(str(value["expires_at"])),
        mode=OverlayMode(str(value["mode"])),
        serendipity=float(value["serendipity"]) if value.get("serendipity") is not None else None,
        max_outrage=float(value["max_outrage"]) if value.get("max_outrage") is not None else None,
    )


def slice_to_dict(value: ShareablePassportSlice) -> dict[str, Any]:
    return to_primitive(value)


def slice_from_dict(value: Mapping[str, Any]) -> ShareablePassportSlice:
    return ShareablePassportSlice(
        owner_id=str(value["owner_id"]),
        passport_id=str(value["passport_id"]),
        passport_version=int(value["passport_version"]),
        topic_targets={str(key): float(item) for key, item in dict(value["topic_targets"]).items()},
        creator_preferences={
            str(key): float(item) for key, item in dict(value.get("creator_preferences", {})).items()
        },
        serendipity=float(value["serendipity"]) if value.get("serendipity") is not None else None,
        expires_at=parse_datetime(str(value["expires_at"])),
        consent_id=str(value["consent_id"]),
        format_preferences={
            str(key): float(item) for key, item in dict(value.get("format_preferences", {})).items()
        },
        hard_exclusions=frozenset(str(item) for item in value.get("hard_exclusions", ())),
    )


def action_from_dict(value: Mapping[str, Any]) -> ProposedAction:
    return ProposedAction(
        id=str(value["id"]),
        destination_id=str(value["destination_id"]),
        action_type=ActionType(str(value["action_type"])),
        target=str(value["target"]),
        reason=str(value["reason"]),
        idempotency_key=str(value["idempotency_key"]),
        reversible=bool(value["reversible"]),
        parameters=dict(value.get("parameters", {})),
    )


def outcome_from_dict(value: Mapping[str, Any]) -> ActionOutcome:
    return ActionOutcome(
        action=action_from_dict(value["action"]),
        status=ActionStatus(str(value["status"])),
        before_state=dict(value.get("before_state", {})),
        after_state=dict(value.get("after_state", {})),
        executed_at=parse_datetime(str(value["executed_at"])),
        platform_reference=str(value["platform_reference"]) if value.get("platform_reference") else None,
        error_code=str(value["error_code"]) if value.get("error_code") else None,
    )


def receipt_from_dict(value: Mapping[str, Any]) -> ActionReceipt:
    return ActionReceipt(
        id=str(value["id"]),
        passport_id=str(value["passport_id"]),
        passport_version=int(value["passport_version"]),
        destination_id=str(value["destination_id"]),
        outcomes=tuple(outcome_from_dict(item) for item in value.get("outcomes", ())),
        issued_at=parse_datetime(str(value["issued_at"])),
        trace_id=str(value["trace_id"]),
        previous_checkpoint_id=(
            str(value["previous_checkpoint_id"]) if value.get("previous_checkpoint_id") else None
        ),
        rollback_caveats=tuple(str(item) for item in value.get("rollback_caveats", ())),
    )
